# 결품 대체품 승인 에이전트 (HITL)

식자재 유통사 BM 업무 — 협력사 결품이 생기면 AI 가 대체품을 고르고 고객사 안내문을 쓴다.
**알레르기·원산지·단가·AI 확신도 기준에 걸린 건만** 고객사에 통보하기 직전에 멈춰 사람 승인을 받고, 나머지는 자동으로 통보한다.

설계와 실행 결과는 [REPORT.md](REPORT.md).

## 실행

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # OPENAI_API_KEY 입력
```

| 명령 | 하는 일 |
|---|---|
| `streamlit run app.py --server.address 127.0.0.1 --server.port 8503` | 승인 데모. 왼쪽 "새 결품 처리" → 승인 대기 탭에서 건을 열어 응답 |
| `python evaluate.py --refresh` | LLM 제안을 새로 만들어 멈춤 기준 조합을 비교 → `output/criteria_eval.md` |
| `python evaluate.py` | 저장된 제안(`output/proposals.json`)으로 비교만 다시 계산 |
| `pytest -q` | 멈춤·재개 경로 테스트 (API 키 불필요) |

API 키 없이 화면만 보려면 가짜 LLM 모드로 띄운다:

```bash
set HITL_FAKE_LLM=1&& set HITL_RUNTIME=runtime_fake&& streamlit run app.py --server.address 127.0.0.1 --server.port 8503
```

## 파일

| 파일 | 내용 |
|---|---|
| `agent.py` | LangGraph 파이프라인 (gather → propose → check → review/send/escalate), SqliteSaver |
| `criteria.py` | 멈춤 기준 4가지와 임계값 |
| `app.py` | Streamlit 승인 화면 |
| `evaluate.py` | 기준 조합별 개입률·놓침·헛멈춤 |
| `data/` | 합성 데이터: 상품 28종, 고객사 4곳, 결품 18건(사람이 붙인 정답 라벨 포함) |
| `runtime/` | 실행 중 생기는 파일 (git 제외): `checkpoints.sqlite` 대기 건, `outbox.jsonl` 고객사 통보 기록, `escalations.jsonl` 반려 건 |

고객사 통보와 발주 변경은 실제 시스템 대신 `runtime/outbox.jsonl` 에 기록하는 것으로 대신한다.
