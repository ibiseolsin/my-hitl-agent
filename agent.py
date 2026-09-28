"""결품 대체품 제안 에이전트 — 고객사에 발주 변경을 통보하기 직전에 사람 승인을 받는다.

gather → propose(LLM) → check ─┬─ (기준 통과) ──────────────→ send ──→ END
                               └─ (기준 걸림) → review ─┬─ approve/edit → send
                                     ↑ (interrupt)      ├─ retry → propose
                                     │                  └─ reject → escalate → END
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, TypedDict

from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

import criteria

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
DATA = ROOT / "data"
RUNTIME = Path(os.environ.get("HITL_RUNTIME", ROOT / "runtime"))
MODEL = "gpt-4.1-mini"
MAX_RETRY = 2   # 다시 판정 상한


def load(name):
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


PRODUCTS = {p["id"]: p for p in load("products")}
CUSTOMERS = {c["id"]: c for c in load("customers")}
EVENTS = {e["id"]: e for e in load("stockouts")}


class State(TypedDict, total=False):
    event: dict
    customer: dict
    original: dict
    candidates: list
    proposal: dict          # substitute_id, reason, confidence, concerns, notice
    stop_reasons: list
    retry_count: int
    instruction: str        # 다시 판정 지시
    decision: dict          # 담당자 응답
    outcome: str            # auto_sent | approved_sent | edited_sent | rejected
    sent: dict


# ───────────────────────── 판단 (LLM) ─────────────────────────

class Proposal(BaseModel):
    substitute_id: str = Field(description="후보 목록의 상품 ID. 쓸 만한 후보가 없으면 NONE")
    reason: str = Field(description="이 후보를 고른 근거. 원 상품과 무엇이 같고 무엇이 다른지 2~3문장")
    confidence: float = Field(description="고객사가 사람 확인 없이 받아도 문제없을 확신도 0~1")
    concerns: list[str] = Field(description="고객사 운영에 영향을 줄 수 있는 차이점. 없으면 빈 목록")
    notice: str = Field(description="고객사 담당자에게 보낼 대체품 안내문 (4~6줄)")


SYSTEM = """당신은 식자재 유통사의 BM(상품 담당)입니다. 협력사 결품이 생긴 주문에 대해
재고가 있는 후보 중에서 고객사에 보낼 대체품 하나를 고르고 안내문을 씁니다.

고를 때 보는 것: 알레르기 유발물질이 새로 들어가는지, 원산지, 단가 차이, 규격·부위·등급,
보관 방식(냉장/냉동), 기능성 규격(저염 등), 그리고 고객사의 운영 특성.

confidence 기준:
- 0.9 이상: 사실상 동등품. 고객사가 차이를 느끼지 못함
- 0.7~0.9: 사소한 차이. 고객사 운영에 영향 없음
- 0.7 미만: 조리 일정·환자식 규격·계약 등급처럼 고객사 운영에 영향을 줄 수 있는 차이가 있음

안내문에는 결품 사유, 대체품명, 달라지는 점(원산지·알레르기·단가 변동은 반드시 명시)을 적습니다.
확인되지 않은 사실을 지어내지 마세요."""


def describe(p, orig=None):
    diff = f", 단가 차 {criteria.price_diff_pct(orig, p):+.1f}%" if orig else ""
    return (f"[{p['id']}] {p['name']} | 규격 {p['spec']} | 원산지 {p['origin']} | "
            f"알레르기 {', '.join(p['allergens']) or '없음'} | {p['storage']} | "
            f"{p['unit_price']:,}원/{p['unit']}{diff}")


def llm_propose(state):
    from langchain_openai import ChatOpenAI

    ev, orig = state["event"], state["original"]
    lines = [
        f"고객사: {state['customer']['name']} ({state['customer']['type']}) — {state['customer']['notes']}",
        f"결품 상품: {describe(orig)}",
        f"주문 수량: {ev['qty']}{orig['unit']}, 납품일 {ev['delivery_date']}",
        f"결품 사유: {ev['reason']}",
        "후보:",
        *[describe(c, orig) for c in state["candidates"]],
    ]
    if state.get("instruction"):
        lines.append(f"\n담당자 재판정 지시: {state['instruction']}")
    llm = ChatOpenAI(model=MODEL, temperature=0).with_structured_output(Proposal)
    out = llm.invoke([("system", SYSTEM), ("user", "\n".join(lines))])
    return out.model_dump()


def fake_propose(state):
    """API 없이 도는 결정적 대역. 테스트와 오프라인 데모용."""
    orig = state["original"]

    def risk(c):
        return (len(set(c["allergens"]) - set(orig["allergens"])),
                criteria.origin_changed(orig, c, None) is not None,
                abs(criteria.price_diff_pct(orig, c)))

    if not state["candidates"]:
        return {"substitute_id": "NONE", "reason": "후보 없음", "confidence": 0.0, "concerns": [], "notice": ""}
    best = min(state["candidates"], key=risk)
    concerns = []
    if best["storage"] != orig["storage"]:
        concerns.append(f"보관 방식 {orig['storage']} → {best['storage']}")
    if "저감" in orig["spec"] and "저감" not in best["spec"]:
        concerns.append("기능성 규격(저염) 미충족")
    return {
        "substitute_id": best["id"],
        "reason": f"{orig['name']} 대신 같은 분류의 {best['name']}을(를) 제안합니다.",
        "confidence": 0.5 if concerns else 0.9,
        "concerns": concerns,
        "notice": f"{orig['name']} 결품으로 {best['name']}(으)로 대체 납품합니다.",
    }


def default_proposer():
    return fake_propose if os.environ.get("HITL_FAKE_LLM") == "1" else llm_propose


# ───────────────────────── 노드 ─────────────────────────

def gather(state):
    ev = state["event"]
    orig = PRODUCTS[ev["product_id"]]
    cands = [p for p in PRODUCTS.values()
             if p["group"] == orig["group"] and p["id"] != orig["id"] and p["in_stock"]]
    return {"customer": CUSTOMERS[ev["customer_id"]], "original": orig, "candidates": cands, "retry_count": 0}


def make_propose(proposer):
    def propose(state):
        p = proposer(state)
        if p["substitute_id"] not in {c["id"] for c in state["candidates"]}:
            p["substitute_id"] = "NONE"
        return {"proposal": p}
    return propose


def check(state):
    sub = PRODUCTS.get(state["proposal"]["substitute_id"])
    reasons = criteria.stop_reasons(state["original"], sub, state["proposal"])
    if state.get("retry_count"):
        # 담당자가 다시 판정을 시킨 건은 결과도 담당자가 본다.
        reasons = reasons + [f"담당자 재판정 요청 ({state['retry_count']}회차)"]
    return {"stop_reasons": reasons}


def review_payload(state):
    """승인 화면에 보일 내용. 요청 원문·핵심 정보·판정과 근거·멈춘 이유·통과 시 결과."""
    ev, orig, prop = state["event"], state["original"], state["proposal"]
    sub = PRODUCTS.get(prop["substitute_id"])
    old = orig["unit_price"] * ev["qty"]
    new = sub["unit_price"] * ev["qty"] if sub else None
    return {
        "event_id": ev["id"],
        "customer": state["customer"],
        "stockout_reason": ev["reason"],
        "qty": ev["qty"],
        "unit": orig["unit"],
        "delivery_date": ev["delivery_date"],
        "original": orig,
        "substitute": sub,
        "proposal": prop,
        "stop_reasons": state["stop_reasons"],
        "if_approved": (
            f"{state['customer']['name']}에 안내문이 발송되고, 발주가 '{orig['name']}'에서 '{sub['name']}'(으)로 "
            f"바뀝니다 (수량 {ev['qty']}{orig['unit']}, 금액 {old:,}원 → {new:,}원, {new - old:+,}원)."
            if sub else "대체품이 없어 승인할 수 없습니다. 반려하면 BM 수동 처리 목록으로 넘어갑니다."
        ),
        "candidates": state["candidates"],
        "retry_left": MAX_RETRY - state.get("retry_count", 0),
    }


def review(state):
    # 재개하면 이 노드가 처음부터 다시 실행된다. interrupt 앞에 바깥으로 나가는 동작을 두지 않는다.
    answer = interrupt(review_payload(state))
    action = answer.get("action")
    if action == "retry" and state.get("retry_count", 0) >= MAX_RETRY:
        answer = {"action": "reject", "reason": f"재판정 한도({MAX_RETRY}회) 초과"}
    update = {"decision": answer}
    if answer["action"] == "edit":
        prop = dict(state["proposal"])
        prop["substitute_id"] = answer.get("substitute_id", prop["substitute_id"])
        prop["notice"] = answer.get("notice", prop["notice"])
        update["proposal"] = prop
    elif answer["action"] == "retry":
        update["instruction"] = answer.get("instruction", "")
        update["retry_count"] = state.get("retry_count", 0) + 1
    return update


def append_jsonl(name, record):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with open(RUNTIME / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def send(state):
    """바깥으로 나가는 유일한 단계: 고객사 안내문 발송 + 발주 변경(시뮬레이션은 outbox 에 기록)."""
    ev, prop = state["event"], state["proposal"]
    sub = PRODUCTS[prop["substitute_id"]]
    action = state.get("decision", {}).get("action")
    outcome = {"approve": "approved_sent", "edit": "edited_sent"}.get(action, "auto_sent")
    record = {
        "event_id": ev["id"], "customer": state["customer"]["name"],
        "from": state["original"]["id"], "to": sub["id"], "qty": ev["qty"],
        "notice": prop["notice"], "outcome": outcome, "at": datetime.now().isoformat(timespec="seconds"),
    }
    append_jsonl("outbox.jsonl", record)
    return {"outcome": outcome, "sent": record}


def escalate(state):
    """반려: 고객사에는 아무것도 보내지 않고 BM 수동 처리 목록에 올린다."""
    ev = state["event"]
    append_jsonl("escalations.jsonl", {
        "event_id": ev["id"], "customer": state["customer"]["name"],
        "reason": state["decision"].get("reason", ""), "at": datetime.now().isoformat(timespec="seconds"),
    })
    return {"outcome": "rejected"}


def after_check(state) -> Literal["review", "send"]:
    return "review" if state["stop_reasons"] else "send"


def after_review(state) -> Literal["send", "propose", "escalate"]:
    return {"approve": "send", "edit": "send", "retry": "propose"}.get(state["decision"]["action"], "escalate")


def build(checkpointer, proposer=None):
    g = StateGraph(State)
    g.add_node("gather", gather)
    g.add_node("propose", make_propose(proposer or default_proposer()))
    g.add_node("check", check)
    g.add_node("review", review)
    g.add_node("send", send)
    g.add_node("escalate", escalate)
    g.add_edge(START, "gather")
    g.add_edge("gather", "propose")
    g.add_edge("propose", "check")
    g.add_conditional_edges("check", after_check)
    g.add_conditional_edges("review", after_review)
    g.add_edge("send", END)
    g.add_edge("escalate", END)
    return g.compile(checkpointer=checkpointer)


def sqlite_saver(path=None):
    path = Path(path or RUNTIME / "checkpoints.sqlite")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Streamlit 은 요청마다 스레드가 달라서 check_same_thread=False 가 필요하다.
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


# ───────────────────────── 운영 도우미 ─────────────────────────

def cfg(event_id):
    return {"configurable": {"thread_id": event_id}}


def start(app, event_id):
    """새 결품 건을 넣는다. 이미 들어간 건은 건드리지 않는다."""
    if app.get_state(cfg(event_id)).values:
        return None
    return app.invoke({"event": EVENTS[event_id]}, cfg(event_id))


def respond(app, event_id, answer):
    return app.invoke(Command(resume=answer), cfg(event_id))


def status(app, event_id):
    """new | pending | done. 다음 단계가 남아 있으면 대기, 비어 있으면 끝."""
    snap = app.get_state(cfg(event_id))
    if not snap.values:
        return "new", None
    if snap.next:
        return "pending", snap.interrupts[0].value if snap.interrupts else None
    return "done", snap.values


def deadline(event):
    """승인 기한: 납품 전날 17시. 넘기면 자동 반려해 BM 이 직접 처리하게 한다."""
    d = datetime.fromisoformat(event["delivery_date"]) - timedelta(days=1)
    return d.replace(hour=17)


def expire_overdue(app, now=None):
    now = now or datetime.now()
    expired = []
    for eid, ev in EVENTS.items():
        if status(app, eid)[0] == "pending" and now > deadline(ev):
            respond(app, eid, {"action": "reject", "reason": "승인 기한 초과 (자동 반려)"})
            expired.append(eid)
    return expired
