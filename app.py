"""승인 데모 — 결품 대체품 제안 중 사람 확인이 필요한 건을 모아 보고 답한다.

    streamlit run app.py --server.address 127.0.0.1 --server.port 8503
    (API 키 없이 보려면 HITL_FAKE_LLM=1)
"""
import json
from datetime import datetime

import pandas as pd
import streamlit as st

import agent

st.set_page_config(page_title="결품 대체품 승인", page_icon="📦", layout="wide", initial_sidebar_state="expanded")


@st.cache_resource
def get_app():
    return agent.build(agent.sqlite_saver())


def read_jsonl(name):
    p = agent.RUNTIME / name
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


app = get_app()
statuses = {eid: agent.status(app, eid) for eid in agent.EVENTS}
new = [e for e, (s, _) in statuses.items() if s == "new"]
pending = {e: p for e, (s, p) in statuses.items() if s == "pending"}
done = {e: v for e, (s, v) in statuses.items() if s == "done"}

# ───────────── 사이드바: 처리 시작과 현황 ─────────────
with st.sidebar:
    st.header("📦 결품 대체품 승인")
    st.caption(f"모델: {'가짜 LLM (오프라인)' if agent.default_proposer() is agent.fake_propose else agent.MODEL}")
    if st.button(f"새 결품 {len(new)}건 처리", disabled=not new, type="primary", use_container_width=True):
        with st.spinner("대체품 고르는 중…"):
            for eid in new:
                agent.start(app, eid)
        st.rerun()
    c1, c2, c3 = st.columns(3)
    c1.metric("미처리", len(new))
    c2.metric("승인 대기", len(pending))
    c3.metric("완료", len(done))
    if st.button("기한 초과 건 정리", use_container_width=True,
                 help="납품 전날 17시가 지난 대기 건을 자동 반려하고 BM 수동 처리로 넘긴다"):
        expired = agent.expire_overdue(app)
        st.toast(f"자동 반려 {len(expired)}건" if expired else "기한 초과 건 없음")
        st.rerun()

tab_wait, tab_done = st.tabs([f"승인 대기 ({len(pending)})", f"처리 결과 ({len(done)})"])

# ───────────── 승인 대기 ─────────────
with tab_wait:
    if not pending:
        st.info("승인을 기다리는 건이 없습니다. 왼쪽에서 새 결품을 처리하세요.")
    else:
        order = sorted(pending, key=lambda e: agent.deadline(agent.EVENTS[e]))
        table = pd.DataFrame([{
            "건": e,
            "고객사": pending[e]["customer"]["name"],
            "결품 → 제안": f"{pending[e]['original']['name']} → "
                          f"{pending[e]['substitute']['name'] if pending[e]['substitute'] else '없음'}",
            "멈춘 이유": " · ".join(pending[e]["stop_reasons"]),
            "승인 기한": agent.deadline(agent.EVENTS[e]).strftime("%m/%d %H:%M"),
        } for e in order])
        st.dataframe(table, hide_index=True, use_container_width=True)

        eid = st.selectbox("열어 볼 건", order,
                           format_func=lambda e: f"{e} · {pending[e]['customer']['name']} · {pending[e]['original']['name']}")
        p = pending[eid]
        orig, sub, prop = p["original"], p["substitute"], p["proposal"]

        st.divider()
        left, right = st.columns([3, 2])
        with left:
            st.subheader(f"{eid} · {p['customer']['name']}")
            st.caption(f"{p['customer']['type']} — {p['customer']['notes']}")
            st.markdown(f"**결품 사유 (협력사 통보 원문)**  \n> {p['stockout_reason']}")
            st.markdown(f"**주문** {orig['name']} {p['qty']}{p['unit']} · 납품일 {p['delivery_date']}")

            st.markdown("**AI 제안과 근거**")
            if sub:
                rows = [("상품", orig["name"], sub["name"]),
                        ("규격", orig["spec"], sub["spec"]),
                        ("원산지", orig["origin"], sub["origin"]),
                        ("알레르기", ", ".join(orig["allergens"]) or "없음", ", ".join(sub["allergens"]) or "없음"),
                        ("보관", orig["storage"], sub["storage"]),
                        ("단가", f"{orig['unit_price']:,}원/{orig['unit']}", f"{sub['unit_price']:,}원/{sub['unit']}")]
                st.dataframe(pd.DataFrame(rows, columns=["항목", "결품 상품", "AI 제안"]),
                             hide_index=True, use_container_width=True)
            st.write(prop["reason"])
            st.caption(f"AI 확신도 {prop['confidence']:.2f}"
                       + (f" · 우려: {' / '.join(prop['concerns'])}" if prop["concerns"] else ""))
            with st.expander("고객사에 보낼 안내문 초안", expanded=True):
                st.text(prop["notice"] or "(없음)")

        with right:
            st.markdown("**멈춘 이유**")
            for r in p["stop_reasons"]:
                st.error(r, icon="⛔")
            st.markdown("**통과시키면**")
            st.warning(p["if_approved"], icon="📤")

            st.markdown("**응답**")
            action = st.radio("응답", ["승인", "수정 후 승인", "반려", "다시 판정"], horizontal=True,
                              label_visibility="collapsed", key=f"act-{eid}")
            answer = None
            if action == "승인":
                if st.button("승인하고 발송", type="primary", disabled=sub is None, key=f"ok-{eid}"):
                    answer = {"action": "approve"}
            elif action == "수정 후 승인":
                ids = [c["id"] for c in p["candidates"]]
                pick = st.selectbox("대체품", ids, index=ids.index(sub["id"]) if sub else 0,
                                    format_func=lambda i: next(f"{c['name']} ({c['unit_price']:,}원)"
                                                               for c in p["candidates"] if c["id"] == i),
                                    key=f"pick-{eid}")
                notice = st.text_area("안내문", prop["notice"], height=160, key=f"notice-{eid}")
                # 대체품만 바꾸고 안내문을 그대로 두면 다른 상품 설명이 고객사에 나간다.
                stale = (sub is None or pick != sub["id"]) and notice.strip() == prop["notice"].strip()
                if stale:
                    st.warning("대체품을 바꿨는데 안내문이 AI 초안 그대로입니다. 상품명·알레르기·단가를 고쳐 주세요.")
                if st.button("수정한 내용으로 발송", type="primary", disabled=stale, key=f"edit-{eid}"):
                    answer = {"action": "edit", "substitute_id": pick, "notice": notice}
            elif action == "반려":
                reason = st.text_input("반려 사유 (필수)", key=f"why-{eid}")
                if st.button("반려하고 수동 처리로", disabled=not reason.strip(), key=f"rej-{eid}"):
                    answer = {"action": "reject", "reason": reason}
            else:
                st.caption(f"남은 재판정 {p['retry_left']}회")
                inst = st.text_input("AI에게 줄 지시", placeholder="예: 우유가 없는 제품으로 다시 골라줘", key=f"inst-{eid}")
                if st.button("다시 판정", disabled=p["retry_left"] <= 0 or not inst.strip(), key=f"retry-{eid}"):
                    answer = {"action": "retry", "instruction": inst}

            if answer:
                with st.spinner("처리 중…"):
                    agent.respond(app, eid, answer)
                st.rerun()

# ───────────── 처리 결과 ─────────────
with tab_done:
    label = {"auto_sent": "자동 발송", "approved_sent": "승인 발송", "edited_sent": "수정 후 발송", "rejected": "반려"}
    if done:
        st.dataframe(pd.DataFrame([{
            "건": e, "고객사": v["customer"]["name"], "결과": label[v["outcome"]],
            "결품": v["original"]["name"],
            "대체품": agent.PRODUCTS[v["sent"]["to"]]["name"] if v.get("sent") else "-",
        } for e, v in sorted(done.items())]), hide_index=True, use_container_width=True)
    st.markdown("**발송함 (고객사 통보·발주 변경 기록)**")
    st.dataframe(pd.DataFrame(read_jsonl("outbox.jsonl")), hide_index=True, use_container_width=True)
    st.markdown("**BM 수동 처리 목록 (반려)**")
    st.dataframe(pd.DataFrame(read_jsonl("escalations.jsonl")), hide_index=True, use_container_width=True)
