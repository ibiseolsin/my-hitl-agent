# PLAN — 결품 대체품 제안 HITL 에이전트

과제 요구(강의 요약: `../modulabs-hitl/HITL_승인에이전트_강의_핵심정리.md`)를 슬라이스로 나눈다.
주제: 식자재 유통(B2B) BM 업무 — 협력사 결품 시 대체품을 골라 고객사에 발주 변경을 통보하기 직전에 사람 승인.

## 1단계 — 제출 가능한 완성본

- [x] **1. 합성 데이터** — 상품 카탈로그·고객사·결품 건(정답 라벨 포함)
  - `python -c "import json;[json.load(open(f'data/{n}.json',encoding='utf-8')) for n in ['products','customers','stockouts']]"` 오류 없음
- [x] **2. HITL 파이프라인** [선행: 1] — 후보 수집 → LLM 선정 → 기준 검사 → (멈춤|자동) → 통보, SqliteSaver
  - `pytest -q` 로 자동 통과·승인·수정·반려·재판정·재시작 후 재개 경로 통과 (가짜 LLM)
- [ ] **3. 기준 검증** [선행: 2] — 기준 조합별 개입률·놓침·헛멈춤 표
  - `python evaluate.py` 가 `output/criteria_eval.md` 생성
  - 막힘: OpenAI 429 project_spend_limit_exceeded (2026-09-28). 코드는 완성, LLM 실행만 남음
- [x] **4. 웹 데모** [선행: 2] — Streamlit 대기 목록·상세·네 가지 응답·처리 결과
  - 127.0.0.1 에서 실행해 대기 건 하나를 승인하고 outbox 에 기록되는 것 확인
- [ ] **5. README·REPORT·공개 저장소** [선행: 3, 4]
  - REPORT 에 구조도·기준·실행 결과·화면 캡처, `gh repo view` 로 공개 확인
