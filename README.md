# Failed Subscription Recovery Agent (Hybrid AI + Deterministic FSM)

A lightweight, rigorous, hybrid Failed Subscription Recovery Agent built with Python 3.11 & FastAPI for a fintech buildathon submission. 

---

## Key Capabilities

1. **Hybrid AI + Rule-Based Engine (`app/ai_decision.py` & `app/decision_engine.py`)**:
   - Leverages **Anthropic Claude API (`claude-sonnet-4-6`)** to analyze charge telemetry and recommend recovery actions (`RETRY`, `ESCALATE`, `ABANDON`).
   - Validates response JSON against Pydantic schema `AIDecisionResult`.
   - Enforces `AI_CONFIDENCE_THRESHOLD = 0.70`.
   - **Deterministic Fallback & Safety Boundary**: Automatically falls back to the rule-based engine if confidence < 0.70, LLM call fails/times out, or AI recommends an action violating hard stopping rules.
2. **Finite State Machine (FSM)**: Enforces strict lifecycle transitions:
   `DETECTED` → `RETRY_SCHEDULED` → `RETRYING` → `RECOVERED` | `ESCALATED` | `ABANDONED`
3. **Dual Attempt Budgets**:
   - Infrastructure faults (`NETWORK_ERROR`): Managed via `infra_attempt_count` (cap: **5 retries**).
   - Customer-fault failures (`INSUFFICIENT_FUNDS`, `CARD_DECLINED`): Managed via `customer_attempt_count` (cap: **3 retries**).
4. **Strict Per-Attempt Idempotency**:
   - `POST /charges/{id}/retry` requires mandatory `Idempotency-Key` header.
5. **Audit Trail Transparency**:
   - Logs `decision_source` (`"ai_decision"` vs `"rule_based_fallback"`), `decision_source_reason`, `ai_confidence`, and `ai_reasoning` for every transition.

---

## Folder & File Structure

```
.
├── app/
│   ├── __init__.py           # Package initializer
│   ├── models.py             # Domain models, Enums, and AuditLogEntry schema
│   ├── storage.py            # Thread-safe in-memory database & idempotency registry
│   ├── state_machine.py      # Finite State Machine validator and audit logger
│   ├── decision_engine.py   # Deterministic rules & hybrid AI coordinator (evaluate_hybrid_decision)
│   ├── ai_decision.py       # LLM client wrapper (claude-sonnet-4-6), prompt builder, & AIDecisionResult schema
│   ├── executor.py          # Idempotent retry execution engine
│   └── main.py              # FastAPI Web API application endpoints
├── tests/
│   ├── __init__.py           # Test package initializer
│   └── test_agent.py         # Pytest suite verifying dual budgets, FSM transitions, idempotency, & AI fallbacks (16 tests passing)
├── requirements.txt          # Python dependencies (fastapi, uvicorn, pydantic, pytest, httpx, anthropic)
├── pyproject.toml            # Project & pytest config
└── README.md                 # Technical architecture & interview quick reference guide
```

---

## Quick Start & Running Tests

### 1. Run Unit & Integration Tests
```bash
pytest -v
```

### 2. Launch Local API Server
```bash
uvicorn app.main:app --reload
```
Interactive OpenAPI Swagger documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).


---

## API Endpoint Reference

- `GET /charges` - List all mock charges with current state, attempt counts, and timing.
- `GET /charges/{id}` - Fetch single charge details.
- `POST /charges/{id}/process` - Evaluate charge using hybrid decision engine (AI + rules fallback).
- `POST /charges/{id}/retry` - Execute idempotent retry attempt (requires `Idempotency-Key` header).
- `GET /charges/{id}/logs` - Retrieve structured audit history.
- `GET /analytics/summary` - **[NEW]** Real-time financial metrics, recovery rate %, AI decision vs. rule fallback count, and `safety_overrides_count`.
- `POST /charges/simulate` - **[NEW]** Dynamically inject a failed charge pre-populated at any attempt count (e.g. 2/3 attempts) for fast demo walkthroughs.
- `POST /mock-data/reset` - Reset mock store to pristine initial state.
