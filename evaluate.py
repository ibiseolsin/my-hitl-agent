"""멈춤 기준 검증 — 같은 결품 18건에 기준 조합을 바꿔 적용해 개입률·놓침·헛멈춤을 센다.

    python evaluate.py            # 저장된 AI 제안(output/proposals.json)으로 계산
    python evaluate.py --refresh  # LLM 을 다시 불러 제안부터 새로 만든다

정답(should_stop)은 data/stockouts.json 에 사람이 붙인 라벨이다. AI 가 예상과 다른 후보를 고르면
정답도 달라지므로(국내산 대신 수입산을 고르면 멈춰야 함) if_substitute 의 라벨을 쓴다.
"""
import argparse
import json
from pathlib import Path

import agent
import criteria

OUT = Path(__file__).parent / "output"
COMBOS = [
    ("기준 없음 (전부 자동)", []),
    ("단가 ±10%", ["price"]),
    ("알레르기 + 원산지", ["allergen", "origin"]),
    ("알레르기 + 원산지 + 단가", ["allergen", "origin", "price"]),
    ("알레르기 + 원산지 + 확신도", ["allergen", "origin", "confidence"]),
    ("v1: 알레르기 + 원산지 + 단가 + 확신도", ["allergen", "origin", "price", "confidence"]),
    ("v1 + 보관 방식", ["allergen", "origin", "storage", "price", "confidence"]),
    ("v2: v1 + 보관 방식, 단가는 총액 1만 원 이상만 ★채택",
     ["allergen", "origin", "storage", "price_amount", "confidence"]),
    ("v2 에서 확신도 뺌", ["allergen", "origin", "storage", "price_amount"]),
    ("전부 멈춤", None),
]


def proposals(refresh):
    path = OUT / "proposals.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    propose = agent.make_propose(agent.default_proposer())
    result = {}
    for eid, ev in agent.EVENTS.items():
        state = {"event": ev, **agent.gather({"event": ev})}
        result[eid] = propose(state)["proposal"]
        print(eid, result[eid]["substitute_id"], result[eid]["confidence"])
    OUT.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def gold(ev, prop):
    """(사람이 봐야 하나, 이유) — AI 가 고른 대체품 기준."""
    g = ev.get("if_substitute", {}).get(prop["substitute_id"], ev)
    return g["should_stop"], g["why"]


def did_stop(ev, prop, use):
    if use is None:
        return True
    orig = agent.PRODUCTS[ev["product_id"]]
    return bool(criteria.stop_reasons(orig, agent.PRODUCTS.get(prop["substitute_id"]), prop, ev["qty"], use))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    props = proposals(ap.parse_args().refresh)
    events = list(agent.EVENTS.values())
    n = len(events)

    rows = ["| 멈춤 기준 | 개입률 | 놓침 | 헛멈춤 | 놓친 건 | 헛멈춘 건 |", "|---|---|---|---|---|---|"]
    for name, use in COMBOS:
        stops = {e["id"]: did_stop(e, props[e["id"]], use) for e in events}
        missed = [e["id"] for e in events if gold(e, props[e["id"]])[0] and not stops[e["id"]]]
        false = [e["id"] for e in events if not gold(e, props[e["id"]])[0] and stops[e["id"]]]
        rows.append(f"| {name} | {sum(stops.values())}/{n} | {len(missed)} | {len(false)} | "
                    f"{', '.join(missed) or '-'} | {', '.join(false) or '-'} |")

    detail = ["| 건 | 고객사 | 결품 → AI 제안 | 확신도 | 걸린 기준 (채택안) | 정답 |", "|---|---|---|---|---|---|"]
    for e in events:
        p = props[e["id"]]
        orig = agent.PRODUCTS[e["product_id"]]
        sub = agent.PRODUCTS.get(p["substitute_id"])
        reasons = criteria.stop_reasons(orig, sub, p, e["qty"])
        detail.append(f"| {e['id']} | {agent.CUSTOMERS[e['customer_id']]['type']} | "
                      f"{orig['id']} → {p['substitute_id']} | {p['confidence']:.2f} | "
                      f"{'; '.join(reasons) or '자동'} | {'멈춤' if gold(e, p)[0] else '자동'} ({gold(e, p)[1]}) |")

    md = "\n".join(["# 멈춤 기준 비교", "", f"결품 {n}건, 모델 {agent.MODEL}, 정답 라벨은 data/stockouts.json (AI 가 고른 대체품 기준).",
                    "", *rows, "", "## 건별 결과 (채택 기준)", "", *detail, ""])
    (OUT / "criteria_eval.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
