# 🛡️ Bounded Recovery | AI Subscription Recovery Platform

[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Pytest](https://img.shields.io/badge/Pytest-18%2F18%20Passed-emerald.svg)](https://docs.pytest.org/)
[![LLM](https://img.shields.io/badge/AI%20Engine-Anthropic%20Claude-purple.svg)](https://www.anthropic.com/)
[![License](https://img.shields.io/badge/License-MIT-orange.svg)]()

> **Bounded Recovery** is an autonomous, hybrid AI-powered subscription recovery platform built with **Python 3.11 & FastAPI**. It resolves involuntary subscription churn, enforces deterministic state machine guardrails, executes exponential backoffs, and eliminates double-charging with per-attempt idempotency.

---

## 📊 Key Financial Impact & Empirical Metrics

In a 55-charge multi-pass terminal execution test across 5 distinct risk buckets:

| Metric | Result | Engineering Significance |
| :--- | :--- | :--- |
| **Baseline Recovery Rate** | **0.00%** | Recovery rate if failed charges are abandoned after 1 attempt |
| **Bounded System Recovery** | **73.48%** | **$138,449.34 revenue secured** out of $188,424.91 total failed volume |
| **Active Stuck Charges** | **0 charges** | **100% of charges** reached terminal states (`RECOVERED` or `ESCALATED`) |
| **Safety Overrides** | **13 Enforced** | Rule engine blocked AI recommendations that attempted to exceed attempt caps |
| **Unit & Integration Tests** | **18 / 18 Passed** | 100% test pass rate verifying FSM, idempotency, backoff, and AI fallbacks |

---

## ⚡ Core Architecture & Capabilities

```
                  +-----------------------+
                  |  DETECTED (Failure)   |
                  +-----------+-----------+
                              |
                              v
                  +-----------------------+
                  |    RETRY_SCHEDULED    | (Exponential Backoff)
                  +-----------+-----------+
                              |
                              v
                  +-----------------------+
                  |       RETRYING        | (Idempotent Execution)
                  +-----------+-----------+
                              |
         +--------------------+--------------------+
         |                    |                    |
         v                    v                    v
  +--------------+     +--------------+     +--------------+
  |  RECOVERED   |     |  ESCALATED   |     |  ABANDONED   |
  | (Secured $ ) |     |  (Cap Hit)   |     | (Unrecovered)|
  +--------------+     +--------------+     +--------------+
```

1. **Deterministic Finite State Machine (FSM)**:
   - Validates all lifecycle transitions. Illegal state jumps raise `InvalidStateTransitionException`.

2. **Dual Attempt Budgets**:
   - **Infrastructure Faults (`NETWORK_ERROR`)**: Capped at **5 retries** (`infra_attempt_count`).
   - **Customer Faults (`INSUFFICIENT_FUNDS`, `CARD_DECLINED`)**: Capped at **3 retries** (`customer_attempt_count`).

3. **Hybrid AI + Rule-Based Guardrails**:
   - Telemetry evaluated via **Anthropic Claude API (`claude-sonnet-4-6`)**.
   - Enforces `AI_CONFIDENCE_THRESHOLD = 0.70`.
   - **Deterministic Fallback**: Gracefully falls back to rules if LLM confidence < 0.70 or API fails.
   - **Safety Boundary**: Intercepts AI retry suggestions if attempt limits are reached (**13 safety overrides** enforced).

4. **Strict Per-Attempt Idempotency**:
   - `POST /charges/{id}/retry` requires mandatory `Idempotency-Key` header to guarantee zero double-charging.

5. **Structured Audit Trail Transparency**:
   - Every transition logs `decision_source` (`"ai_decision"` vs `"rule_based_fallback"`), `decision_source_reason`, `ai_confidence`, and `ai_reasoning`.

---

## 📁 Repository & File Structure

```
.
├── app/
│   ├── models.py             # Domain models, Enums, & AuditLogEntry schemas
│   ├── storage.py            # Thread-safe in-memory store with RLock synchronization
│   ├── state_machine.py      # Finite State Machine validator & audit logger
│   ├── decision_engine.py   # Deterministic rules & hybrid AI coordinator
│   ├── ai_decision.py       # Anthropic Claude LLM client wrapper & confidence evaluator
│   ├── executor.py          # Idempotent retry execution engine
│   ├── main.py              # FastAPI REST endpoints & static server
│   └── static/              # Razorpay glassmorphism live dashboard (HTML, CSS, JS)
├── tests/
│   ├── test_agent.py         # Pytest suite (18 unit/integration tests)
│   └── generate_batch.py    # 55-charge multi-pass batch test simulator
├── batch_test_results.json   # Evidence payload of 100% terminal batch execution
├── pyproject.toml            # Project setup & pytest config
├── requirements.txt          # Dependencies (fastapi, uvicorn, pydantic, pytest, httpx, anthropic)
└── README.md                 # Project documentation
```

---

## 🚀 Quick Start & Running Tests

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Full Test Suite
```bash
pytest -v
```

### 3. Run End-to-End Batch Simulation
```bash
python tests/generate_batch.py
```

### 4. Launch Live Server & Dashboard
```bash
python -m uvicorn app.main:app --reload
```
- **Live Dashboard**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **OpenAPI Swagger Docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

---

## 🔌 REST API Reference

- `GET /charges` - Retrieve all tracked failed charges.
- `GET /charges/{id}` - Fetch single charge details by ID.
- `POST /charges/{id}/process` - Evaluate charge using hybrid decision engine.
- `POST /charges/{id}/retry` - Execute retry attempt (requires `Idempotency-Key` header).
- `GET /charges/{id}/logs` - Fetch structured audit history.
- `GET /analytics/summary` - Real-time metrics, recovery rates, AI counts, and safety overrides.
- `POST /charges/simulate` - Inject simulated charge with pre-populated attempt counts.
- `POST /mock-data/reset` - Reset store to pristine initial state.
