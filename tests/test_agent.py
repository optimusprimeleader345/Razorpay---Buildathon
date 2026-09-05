"""Comprehensive pytest suite for Failed Subscription Recovery Agent."""

import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from app.main import app
from app.storage import storage
from app.models import SubscriptionState, FailureReason
from app.decision_engine import calculate_backoff_delay
from app.state_machine import InvalidStateTransitionException, transition

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_db_before_test():
    """Autouse fixture ensuring pristine database state before every test."""
    storage.reset()


def test_dual_attempt_budget_independence():
    """Verifies infrastructure retries and customer-fault retries use independent budgets."""
    # Process chg_102 (NETWORK_ERROR -> infra fault)
    res_infra = client.post("/charges/chg_102/process")
    assert res_infra.status_code == 200

    # Execute infra retry attempt 1 (fails)
    res1 = client.post("/charges/chg_102/retry", headers={"Idempotency-Key": "key_infra_1"}, json={"force_outcome": "FAILURE"})
    assert res1.status_code == 200
    charge_infra = client.get("/charges/chg_102").json()
    assert charge_infra["infra_attempt_count"] == 1
    assert charge_infra["customer_attempt_count"] == 0

    # Process chg_101 (INSUFFICIENT_FUNDS -> customer fault)
    client.post("/charges/chg_101/process")
    res2 = client.post("/charges/chg_101/retry", headers={"Idempotency-Key": "key_cust_1"}, json={"force_outcome": "FAILURE"})
    assert res2.status_code == 200
    charge_cust = client.get("/charges/chg_101").json()
    assert charge_cust["customer_attempt_count"] == 1
    assert charge_cust["infra_attempt_count"] == 0


def test_infra_fault_max_retries_escalation():
    """Verifies NETWORK_ERROR allows up to 5 attempts before ESCALATED."""
    client.post("/charges/chg_102/process")

    # Perform 5 failed retries
    for i in range(1, 6):
        client.post("/charges/chg_102/retry", headers={"Idempotency-Key": f"key_net_{i}"}, json={"force_outcome": "FAILURE"})

    charge = client.get("/charges/chg_102").json()
    assert charge["infra_attempt_count"] == 5
    assert charge["state"] == SubscriptionState.ESCALATED.value


def test_customer_fault_max_retries_escalation():
    """Verifies customer-fault failure (INSUFFICIENT_FUNDS) caps at 3 attempts before ESCALATED."""
    client.post("/charges/chg_101/process")

    # Perform 3 failed retries
    for i in range(1, 4):
        client.post("/charges/chg_101/retry", headers={"Idempotency-Key": f"key_funds_{i}"}, json={"force_outcome": "FAILURE"})

    charge = client.get("/charges/chg_101").json()
    assert charge["customer_attempt_count"] == 3
    assert charge["state"] == SubscriptionState.ESCALATED.value


def test_hard_failure_immediate_escalation():
    """Verifies CARD_EXPIRED immediately escalates to ESCALATED without retries."""
    res = client.post("/charges/chg_103/process")
    assert res.status_code == 200
    charge = client.get("/charges/chg_103").json()
    assert charge["state"] == SubscriptionState.ESCALATED.value
    assert charge["customer_attempt_count"] == 0
    assert charge["infra_attempt_count"] == 0


def test_idempotency_prevents_duplicate_charge():
    """Verifies duplicate requests with the same idempotency key return cached result without mutating counters."""
    client.post("/charges/chg_101/process")

    idempotency_key = "idemp_unique_999"

    # First attempt
    res1 = client.post("/charges/chg_101/retry", headers={"Idempotency-Key": idempotency_key}, json={"force_outcome": "FAILURE"})
    data1 = res1.json()
    assert data1["idempotent_replay"] is False
    attempt_count_1 = data1["result"]["charge"]["customer_attempt_count"]

    # Second attempt (Duplicate key)
    res2 = client.post("/charges/chg_101/retry", headers={"Idempotency-Key": idempotency_key}, json={"force_outcome": "FAILURE"})
    data2 = res2.json()
    assert data2["idempotent_replay"] is True
    attempt_count_2 = data2["result"]["charge"]["customer_attempt_count"]

    # Verify attempt count did not change
    assert attempt_count_1 == attempt_count_2 == 1


def test_backoff_timing_and_audit_logs():
    """Verifies backoff delay calculation and audit log records."""
    # INSUFFICIENT_FUNDS attempt 0 backoff delay should be 120s
    delay = calculate_backoff_delay(FailureReason.INSUFFICIENT_FUNDS, 0)
    assert delay == 120

    res_proc = client.post("/charges/chg_101/process")
    assert res_proc.status_code == 200

    logs = client.get("/charges/chg_101/logs").json()
    assert len(logs) == 1
    assert logs[0]["new_state"] == SubscriptionState.RETRY_SCHEDULED.value
    assert logs[0]["next_retry_delay_seconds"] == 120
    assert logs[0]["next_retry_at"] is not None


def test_escalation_audit_action_note():
    """Verifies ESCALATED audit log contains the support queue dispatch note."""
    client.post("/charges/chg_103/process")  # CARD_EXPIRED -> immediate ESCALATED

    logs = client.get("/charges/chg_103/logs").json()
    assert len(logs) == 1
    assert logs[0]["new_state"] == SubscriptionState.ESCALATED.value
    assert logs[0]["action_dispatch_note"] == "would notify support queue: subscription-recovery-escalations"


def test_state_machine_invalid_transition():
    """Verifies illegal state transitions raise InvalidStateTransitionException."""
    charge = storage.get_charge("chg_101")
    assert charge.state == SubscriptionState.DETECTED

    # DETECTED -> RECOVERED is illegal
    with pytest.raises(InvalidStateTransitionException):
        transition(charge, SubscriptionState.RECOVERED, "TEST", "TEST", "TEST")


def test_successful_recovery_transition():
    """Verifies RETRYING -> RECOVERED transition when retry succeeds."""
    client.post("/charges/chg_101/process")
    res = client.post("/charges/chg_101/retry", headers={"Idempotency-Key": "rec_key_1"}, json={"force_outcome": "SUCCESS"})
    assert res.status_code == 200
    charge = client.get("/charges/chg_101").json()
    assert charge["state"] == SubscriptionState.RECOVERED.value

    # Terminal state check: attempting another retry on RECOVERED charge must be rejected
    res_terminal = client.post("/charges/chg_101/retry", headers={"Idempotency-Key": "rec_key_2"})
    assert res_terminal.status_code == 400
    assert "cannot be retried" in res_terminal.json()["detail"]


def test_abandoned_state_transition():
    """Verifies state transition to ABANDONED and terminal state enforcement."""
    charge = storage.get_charge("chg_104")
    # DETECTED -> ABANDONED transition
    updated = transition(charge, SubscriptionState.ABANDONED, "USER_CANCELLED", "ABANDON_RECOVERY", "Customer cancelled account.")
    assert updated.state == SubscriptionState.ABANDONED

    # Terminal state check: ABANDONED state rejects further transitions
    with pytest.raises(InvalidStateTransitionException):
        transition(updated, SubscriptionState.RETRYING, "RETRY", "RETRY", "RETRY")


def test_direct_retry_from_detected_state():
    """Verifies executing retry directly on DETECTED charge without prior /process call."""
    res = client.post("/charges/chg_104/retry", headers={"Idempotency-Key": "direct_retry_1"}, json={"force_outcome": "SUCCESS"})
    assert res.status_code == 200
    charge = client.get("/charges/chg_104").json()
    assert charge["state"] == SubscriptionState.RECOVERED.value


def test_mock_data_reset_endpoint():
    """Verifies /mock-data/reset restores clean state."""
    # Mutate data
    client.post("/charges/chg_101/process")
    assert client.get("/charges/chg_101").json()["state"] == SubscriptionState.RETRY_SCHEDULED.value

    # Reset
    res_reset = client.post("/mock-data/reset")
    assert res_reset.status_code == 200
    assert client.get("/charges/chg_101").json()["state"] == SubscriptionState.DETECTED.value


def test_ai_decision_high_confidence(mocker=None):
    """Verifies that high-confidence AI decision (>= 0.70) is accepted with decision_source='ai_decision'."""
    from unittest.mock import patch
    from app.ai_decision import AIDecisionEvaluation, AIDecisionResult

    mock_eval = AIDecisionEvaluation(
        success=True,
        result=AIDecisionResult(
            recommended_action="RETRY",
            confidence=0.92,
            reasoning="Customer has strong payment history; soft failure likely transient."
        )
    )

    with patch("app.decision_engine.ai_evaluate", return_value=mock_eval):
        res = client.post("/charges/chg_101/process")
        assert res.status_code == 200
        logs = client.get("/charges/chg_101/logs").json()
        assert len(logs) == 1
        assert logs[0]["decision_source"] == "ai_decision"
        assert logs[0]["ai_confidence"] == 0.92
        assert "Customer has strong payment history" in logs[0]["ai_reasoning"]


def test_ai_decision_low_confidence_fallback():
    """Verifies that low-confidence AI decision (< 0.70) triggers rule_based_fallback."""
    from unittest.mock import patch
    from app.ai_decision import AIDecisionEvaluation, AIDecisionResult

    mock_eval = AIDecisionEvaluation(
        success=True,
        result=AIDecisionResult(
            recommended_action="RETRY",
            confidence=0.55,
            reasoning="Uncertain whether customer will top up account."
        )
    )

    with patch("app.decision_engine.ai_evaluate", return_value=mock_eval):
        res = client.post("/charges/chg_101/process")
        assert res.status_code == 200
        logs = client.get("/charges/chg_101/logs").json()
        assert len(logs) == 1
        assert logs[0]["decision_source"] == "rule_based_fallback"
        assert "low confidence: 0.55" in logs[0]["decision_source_reason"]


def test_ai_decision_llm_failure_fallback():
    """Verifies that LLM error/timeout triggers rule_based_fallback."""
    from unittest.mock import patch
    from app.ai_decision import AIDecisionEvaluation

    mock_eval = AIDecisionEvaluation(
        success=False,
        error_message="LLM call failed: Connection timeout after 5.0s"
    )

    with patch("app.decision_engine.ai_evaluate", return_value=mock_eval):
        res = client.post("/charges/chg_101/process")
        assert res.status_code == 200
        logs = client.get("/charges/chg_101/logs").json()
        assert len(logs) == 1
        assert logs[0]["decision_source"] == "rule_based_fallback"
        assert "Connection timeout" in logs[0]["decision_source_reason"]


def test_ai_cannot_override_stopping_rules():
    """Verifies that AI cannot override rule-based stopping rules (e.g. recommending RETRY on 3/3 attempts)."""
    from unittest.mock import patch
    from app.ai_decision import AIDecisionEvaluation, AIDecisionResult

    # Set chg_101 customer_attempt_count to 3
    charge = storage.get_charge("chg_101")
    charge.customer_attempt_count = 3
    charge.state = SubscriptionState.RETRY_SCHEDULED
    storage.save_charge(charge)

    mock_eval = AIDecisionEvaluation(
        success=True,
        result=AIDecisionResult(
            recommended_action="RETRY",
            confidence=0.98,
            reasoning="AI confidently recommends retrying one more time."
        )
    )

    with patch("app.decision_engine.ai_evaluate", return_value=mock_eval):
        res = client.post("/charges/chg_101/retry", headers={"Idempotency-Key": "safety_key_1"}, json={"force_outcome": "FAILURE"})
        assert res.status_code == 200
        logs = client.get("/charges/chg_101/logs").json()
        # Find the final failed retry log
        last_log = logs[-1]
        assert last_log["new_state"] == SubscriptionState.ESCALATED.value
        assert last_log["decision_source"] == "rule_based_fallback"
        assert "AI recommended RETRY but rule engine enforced ESCALATED" in last_log["decision_source_reason"]


def test_analytics_summary_endpoint():
    """Verifies GET /analytics/summary calculates volume, recovery rate, and safety_overrides_count."""
    # Recover one charge
    client.post("/charges/chg_101/process")
    client.post("/charges/chg_101/retry", headers={"Idempotency-Key": "rec_key_analytics"}, json={"force_outcome": "SUCCESS"})

    res = client.get("/analytics/summary")
    assert res.status_code == 200
    data = res.json()
    assert "total_failed_volume" in data
    assert data["total_recovered_volume"] == 49.99
    assert data["recovery_rate_percentage"] > 0
    assert "baseline_recovery_rate_percentage" in data
    assert "recovered_on_first_attempt_count" in data
    assert "safety_overrides_count" in data
    assert data["charges_by_state"]["RECOVERED"] == 1


def test_simulate_charge_with_prepopulated_attempts():
    """Verifies POST /charges/simulate creates a charge pre-populated at 2/3 attempts for fast demo escalation."""
    sim_payload = {
        "amount": 250.00,
        "currency": "USD",
        "failure_reason": "INSUFFICIENT_FUNDS",
        "customer_attempt_count": 2,
        "state": "RETRY_SCHEDULED"
    }

    res_sim = client.post("/charges/simulate", json=sim_payload)
    assert res_sim.status_code == 200
    charge_data = res_sim.json()
    charge_id = charge_data["id"]
    assert charge_data["customer_attempt_count"] == 2
    assert charge_data["amount"] == 250.00

    # 1 retry attempt fails -> should immediately transition to ESCALATED (3/3 attempts)
    res_retry = client.post(f"/charges/{charge_id}/retry", headers={"Idempotency-Key": "sim_fast_escalate_key"}, json={"force_outcome": "FAILURE"})
    assert res_retry.status_code == 200
    updated_charge = client.get(f"/charges/{charge_id}").json()
    assert updated_charge["customer_attempt_count"] == 3
    assert updated_charge["state"] == SubscriptionState.ESCALATED.value
