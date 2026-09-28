"""멈춤 기준 — 사람 승인이 필요한지 계산 가능한 형태로 판정한다.

각 기준은 (원 상품, 대체품, AI 제안, 주문 수량) 을 받아 걸리면 사유 문자열을, 아니면 None 을 돌려준다.
evaluate.py 가 같은 함수를 조합해 개입률·놓침·헛멈춤을 비교한다.
"""

PRICE_DIFF_LIMIT = 10.0   # 단가 차 허용 한도(%)
AMOUNT_DIFF_MIN = 10000   # 단가 차가 한도를 넘어도 총액 차가 이보다 작으면 넘긴다(원)
CONFIDENCE_MIN = 0.7      # AI 확신도 하한


def price_diff_pct(orig, sub):
    return (sub["unit_price"] - orig["unit_price"]) / orig["unit_price"] * 100


def allergen_added(orig, sub, proposal, qty):
    """학교·외식은 알레르기 정보를 공지한다. 새로 들어간 유발물질은 식품 사고로 이어진다."""
    added = [a for a in sub["allergens"] if a not in orig["allergens"]]
    return f"알레르기 유발물질 추가: {', '.join(added)}" if added else None


def origin_changed(orig, sub, proposal, qty):
    """집단급식소는 원산지를 식단표·배식대에 표시한다. 바뀌면 고객사가 표시를 고쳐야 한다."""
    if orig["origin"] == sub["origin"]:
        return None
    # 같은 국내산 안에서 산지만 바뀐 경우(해남 → 제주)는 표시가 바뀌지 않는다.
    if orig["origin"].startswith("국내산") and sub["origin"].startswith("국내산"):
        return None
    return f"원산지 변경: {orig['origin']} → {sub['origin']}"


def price_over_limit(orig, sub, proposal, qty):
    """(v1) 계약 단가와 크게 다르면 고객사 정산·원가에 영향을 준다."""
    pct = price_diff_pct(orig, sub)
    return f"단가 차 {pct:+.1f}% (한도 ±{PRICE_DIFF_LIMIT:.0f}%)" if abs(pct) > PRICE_DIFF_LIMIT else None


def price_and_amount_over_limit(orig, sub, proposal, qty):
    """(v2) 단가 차가 커도 총액 차가 작으면(소스 2병에 3,200원) 사람이 볼 가치가 없다."""
    amount = (sub["unit_price"] - orig["unit_price"]) * qty
    if price_over_limit(orig, sub, proposal, qty) and abs(amount) >= AMOUNT_DIFF_MIN:
        return f"단가 차 {price_diff_pct(orig, sub):+.1f}%, 총액 {amount:+,}원"
    return None


def storage_changed(orig, sub, proposal, qty):
    """(v2) 냉장 ↔ 냉동은 해동 시간·보관 설비가 달라 고객사 조리 일정이 어긋난다."""
    return f"보관 방식 변경: {orig['storage']} → {sub['storage']}" if orig["storage"] != sub["storage"] else None


def low_confidence(orig, sub, proposal, qty):
    """규칙으로 못 잡는 차이(기능성 규격, 계약 등급)는 AI 가 확신하지 못한 것으로 잡는다."""
    c = proposal["confidence"]
    return f"AI 확신도 {c:.2f} (<{CONFIDENCE_MIN})" if c < CONFIDENCE_MIN else None


CRITERIA = {
    "allergen": allergen_added,
    "origin": origin_changed,
    "price": price_over_limit,
    "price_amount": price_and_amount_over_limit,
    "storage": storage_changed,
    "confidence": low_confidence,
}
ADOPTED = ["allergen", "origin", "storage", "price_amount", "confidence"]


def stop_reasons(orig, sub, proposal, qty, use=ADOPTED):
    """걸린 기준의 사유 목록. 대체품이 없으면 그 자체가 멈춤 사유다."""
    if sub is None:
        return ["적합한 대체품 없음"]
    return [r for r in (CRITERIA[k](orig, sub, proposal, qty) for k in use) if r]
