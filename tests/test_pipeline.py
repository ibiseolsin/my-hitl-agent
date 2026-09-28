"""가짜 LLM 으로 멈춤·재개 경로를 확인한다. API 키 없이 돈다."""
import json
from datetime import datetime

import pytest

import agent


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "RUNTIME", tmp_path)
    saver = agent.sqlite_saver(tmp_path / "cp.sqlite")
    yield agent.build(saver, proposer=agent.fake_propose)
    saver.conn.close()


def lines(tmp_path, name):
    p = tmp_path / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def test_auto_path_sends_without_human(app, tmp_path):
    agent.start(app, "S01")
    state, values = agent.status(app, "S01")
    assert state == "done" and values["outcome"] == "auto_sent"
    assert [r["to"] for r in lines(tmp_path, "outbox.jsonl")] == ["P102"]


def test_risky_case_stops_before_sending(app, tmp_path):
    agent.start(app, "S03")
    state, payload = agent.status(app, "S03")
    assert state == "pending"
    assert any("우유" in r for r in payload["stop_reasons"])
    assert "발주가" in payload["if_approved"]
    assert lines(tmp_path, "outbox.jsonl") == []   # 멈춘 동안 아무것도 나가지 않는다


def test_approve(app, tmp_path):
    agent.start(app, "S03")
    agent.respond(app, "S03", {"action": "approve"})
    assert agent.status(app, "S03")[1]["outcome"] == "approved_sent"
    assert len(lines(tmp_path, "outbox.jsonl")) == 1


def test_edit_changes_substitute_and_notice(app, tmp_path):
    agent.start(app, "S06")
    agent.respond(app, "S06", {"action": "edit", "substitute_id": "P703", "notice": "수정한 안내문"})
    sent = lines(tmp_path, "outbox.jsonl")[0]
    assert (sent["to"], sent["notice"], sent["outcome"]) == ("P703", "수정한 안내문", "edited_sent")


def test_reject_escalates_and_sends_nothing(app, tmp_path):
    agent.start(app, "S02")
    agent.respond(app, "S02", {"action": "reject", "reason": "고객사와 통화 후 결정"})
    assert agent.status(app, "S02")[1]["outcome"] == "rejected"
    assert lines(tmp_path, "outbox.jsonl") == []
    assert lines(tmp_path, "escalations.jsonl")[0]["reason"] == "고객사와 통화 후 결정"


def test_retry_comes_back_to_human_and_has_limit(app, tmp_path):
    agent.start(app, "S03")
    agent.respond(app, "S03", {"action": "retry", "instruction": "우유 없는 제품으로"})
    state, payload = agent.status(app, "S03")
    assert state == "pending" and payload["retry_left"] == 1
    assert any("재판정" in r for r in payload["stop_reasons"])
    agent.respond(app, "S03", {"action": "retry", "instruction": "다시"})
    agent.respond(app, "S03", {"action": "retry", "instruction": "또"})   # 한도 초과 → 반려
    assert agent.status(app, "S03")[1]["outcome"] == "rejected"


def test_pending_case_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "RUNTIME", tmp_path)
    saver = agent.sqlite_saver(tmp_path / "cp.sqlite")
    agent.start(agent.build(saver, proposer=agent.fake_propose), "S11")
    saver.conn.close()                                   # 프로그램 종료

    saver = agent.sqlite_saver(tmp_path / "cp.sqlite")   # 다시 켬
    app = agent.build(saver, proposer=agent.fake_propose)
    assert agent.status(app, "S11")[0] == "pending"
    agent.respond(app, "S11", {"action": "approve"})
    assert agent.status(app, "S11")[1]["outcome"] == "approved_sent"
    saver.conn.close()


def test_overdue_pending_is_auto_rejected(app, tmp_path):
    agent.start(app, "S03")   # 납품 10/2 → 기한 10/1 17:00
    assert agent.expire_overdue(app, now=datetime(2026, 10, 1, 12)) == []
    assert agent.expire_overdue(app, now=datetime(2026, 10, 1, 18)) == ["S03"]
    assert "기한 초과" in lines(tmp_path, "escalations.jsonl")[0]["reason"]


def test_start_is_idempotent(app, tmp_path):
    agent.start(app, "S01")
    assert agent.start(app, "S01") is None
    assert len(lines(tmp_path, "outbox.jsonl")) == 1


def test_if_approved_follows_the_chosen_substitute():
    ev, cust = agent.EVENTS["S06"], agent.CUSTOMERS["C04"]
    orig = agent.PRODUCTS["P701"]
    text = agent.if_approved(cust, orig, agent.PRODUCTS["P703"], ev["qty"])
    assert "프리미엄 사누끼 우동면" in text and "210,000원 → 245,000원, +35,000원" in text
    assert "승인할 수 없습니다" in agent.if_approved(cust, orig, None, ev["qty"])
