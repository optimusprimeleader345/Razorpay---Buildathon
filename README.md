# Recoup — Bounded AI Recovery for Failed Subscription Payments

**Track:** AI Revenue Recovery | **Built for:** Razorpay AI Buildathon

Recoup is an agent that automatically recovers failed subscription payments — deciding whether to retry, wait, or escalate to a human — using an AI decision layer that is always advisory and never authoritative. A deterministic rule engine enforces hard safety limits underneath it, so the AI can help make smarter decisions but can never cause an unsafe or duplicate action.

---

## The Problem

Failed recurring payments (insufficient funds, expired cards, declined charges, network errors) are one of the most common causes of involuntary customer churn. Most existing approaches fall into one of two traps:

1. **Dumb fixed retries** — retry blindly on a schedule regardless of why the payment failed, annoying customers and wasting gateway fees.
2. **Fully autonomous AI agents** — let an LLM decide freely, with no guardrail against a bad or hallucinated decision — risky when the decision touches real money.

Recoup avoids both traps with a hybrid design.

---

## How It Works

1. **Detect** — A failed charge enters the system with a failure reason, amount, and attempt history.
2. **Decide** — An AI layer (Claude) evaluates the situation and recommends an action with a confidence score and reasoning. This recommendation is only accepted if it falls within limits the rule engine already allows.
3. **Enforce** — A deterministic rule engine enforces hard stopping rules: a maximum of 3 retries for customer-fault failures (e.g. insufficient funds) and 5 for infrastructure-fault failures (e.g. network errors), since infra issues are cheaper and safer to retry more of. If the AI is uncertain (confidence below threshold), times out, or recommends something the rules don't allow, the system automatically falls back to rule-based logic instead.
4. **Act** — The charge either retries (with a reason-specific backoff delay), recovers, or escalates to a human queue.
5. **Log** — Every single decision, whether AI-driven or rule-driven, is written to a structured audit trail with a timestamp, reasoning, and outcome. Nothing happens invisibly.

Every retry is also protected by an idempotency key, so a duplicate request (e.g. a retried API call) can never cause a customer to be charged twice.

---

## Why This Design

The hardest problem wasn't making the AI produce good recommendations — it was making sure a bad AI recommendation could never cause real harm. Recoup solves this by treating the AI purely as an advisor operating inside boundaries it cannot cross:

- If the AI's confidence is below 0.70, the system falls back to rules.
- If the LLM call fails or times out, the system falls back to rules.
- If the AI recommends an action the rule engine doesn't allow (e.g. retrying a charge already at its cap), the rule engine overrides it and enforces the safe outcome instead.

In testing, this safety layer caught and blocked **13 unsafe AI recommendations** — proof the boundary isn't just theoretical.

---

## Results (Verified, 55+ Synthetic Charges)

| Metric | Value |
| :--- | :--- |
| **Total Failed Volume** | $178,474.42 |
| **Total Recovered Volume** | $139,470.32 |
| **Recovery Rate (Recoup)** | **78.15%** |
| **Recovery Rate (Baseline — no retry system)** | **0.00%** |
| **AI-Driven Decisions** | 80 |
| **Rule-Based Fallback Decisions** | 165 |
| **Safety Overrides Enforced** | 13 |
| **Automated Test Coverage** | 18 / 18 passing |

*Decision counts exceed the number of charges because a single charge can pass through multiple decision points over its lifecycle (e.g. detection, retry attempt 1, retry attempt 2).*

Raw evidence is saved in [`batch_test_results.json`](batch_test_results.json).

---

## Architecture

```text
app/
├── models.py           # Data models: FailedCharge, AuditLogEntry, enums
├── storage.py          # Thread-safe in-memory store (charges, audit logs, idempotency registry)
├── state_machine.py    # Finite state machine — validates and logs every transition
├── decision_engine.py # Deterministic rule engine: stopping rules, backoff, hybrid coordinator
├── ai_decision.py     # AI decision layer (Claude) — confidence-gated, schema-validated
├── executor.py        # Idempotent retry executor with thread-safe locking
└── main.py            # FastAPI endpoints + live ops dashboard

tests/
├── test_agent.py       # 18 unit/integration tests
└── generate_batch.py   # 55+ charge batch simulation across 5 risk buckets
```

State machine: `DETECTED` → `RETRY_SCHEDULED` → `RETRYING` → `RECOVERED` | `ESCALATED` | `ABANDONED`  
*Terminal states reject further transitions.*

---

## Tech Stack

- **Backend**: Python 3.11, FastAPI, Pydantic v2
- **AI**: Anthropic Claude (`claude-sonnet-4-6`), used as a confidence-gated advisor, not an autonomous actor
- **Storage**: In-memory (deliberate scope choice for this build — see Limitations)
- **Frontend**: Live ops console showing real-time metrics, a charge table, an audit log drawer, and a charge simulator

---

## Running It Locally

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your Anthropic API key
Create a `.env` file in the project root:
```env
ANTHROPIC_API_KEY=your-key-here
```

### 3. Start the server
```bash
python -m uvicorn app.main:app --reload
```
- **Live dashboard**: [http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- **Interactive API docs**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

### 4. Try it yourself
1. Use **Inject Failed Charge** to create a charge already near its retry cap (e.g. 2/3 attempts).
2. Click **Process AI**, then **Retry**, and watch it either recover or get correctly escalated once it hits the limit.
3. Click **Logs** on any charge to see its full audit trail, including whether a safety override occurred.

### 5. Run the tests
```bash
pytest -v                    # 18 unit/integration tests
python tests/generate_batch.py # full batch simulation, writes batch_test_results.json
```

---

## Build Challenges

1. **Making sure retries don't sneak past their limit**: I had to trace through the exact order of operations to confirm the attempt counter increments before the stopping-rule check runs — otherwise a charge could slip through for one extra retry. Added a dedicated test for this.
2. **Not every failure deserves the same number of chances**: Early on, every failure type shared one retry budget. A network glitch isn't the customer's fault and is cheap to retry — it shouldn't cost the same "strikes" as insufficient funds. Split into two independent counters: 3 for customer-fault failures, 5 for infrastructure faults.
3. **Keeping the AI helpful without letting it be risky**: The real challenge wasn't getting good AI recommendations, it was guaranteeing a bad one could never cause harm. Solved with a hybrid design where the AI can only choose within rule-engine-approved bounds, with automatic fallback on low confidence, API failure, or an unsafe suggestion.
4. **Stopping accidental double-charges**: A duplicate retry request could double-charge a customer. Fixed with per-attempt idempotency keys and thread-safe locking around shared state.
5. **Getting honest batch numbers**: My first batch test run showed most charges stuck mid-flight because it only processed one pass. Fixed by looping until every charge reaches a real terminal outcome, which produced accurate, honest recovery numbers instead of a partial snapshot.

---

## Limitations & What's Next

This is a scoped 2-week build, and some things were deliberately left out to keep the core logic rigorous rather than spreading thin:
- **In-memory storage, not a persistent database**: In production this would move to Postgres, with the idempotency registry potentially backed by Redis.
- **No real payment gateway integration**: Retries are simulated; a production version would integrate with an actual payment processor's retry/capture APIs.
- **Single failure-recovery loop**: This intentionally covers one narrow, well-tested flow rather than a broad platform, per the buildathon's own guidance to prioritize depth over breadth.

With more time, the next additions would be: a persistent store, real gateway integration, and a notification/escalation integration for the human queue currently only logged as a stub.

---

## Tests

**18 passed in 1.68s**

Covers: dual attempt budget independence, stopping rules for both fault types, idempotency guarantees, backoff timing, audit log correctness, AI high/low-confidence paths, LLM failure fallback, and the AI safety-override guarantee.
