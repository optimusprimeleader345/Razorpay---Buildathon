"""Idempotent retry executor simulating subscription payment recovery operations."""

from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.models import FailedCharge, SubscriptionState, FailureReason
from app.storage import storage
from app.state_machine import transition, InvalidStateTransitionException
from app.decision_engine import evaluate_hybrid_decision


class RetryExecutor:
    """Handles retry executions with strict per-attempt idempotency guarantees."""

    @staticmethod
    def execute_retry(
        charge_id: str,
        idempotency_key: str,
        force_outcome: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a retry attempt for a charge idempotently.
        """
        with storage._lock:
            # 1. Idempotency Registry Check
            cached_result = storage.get_idempotent_result(idempotency_key)
            if cached_result is not None:
                return {
                    "idempotent_replay": True,
                    "idempotency_key": idempotency_key,
                    "result": cached_result
                }

            # 2. Fetch target charge
            charge = storage.get_charge(charge_id)
            if not charge:
                raise ValueError(f"Charge with ID '{charge_id}' not found.")

            # Ensure charge is in a retryable state
            if charge.state not in (SubscriptionState.RETRY_SCHEDULED, SubscriptionState.DETECTED):
                raise InvalidStateTransitionException(
                    f"Charge '{charge_id}' is in terminal state '{charge.state.value}' and cannot be retried."
                )

            # 3. Transition: RETRY_SCHEDULED -> RETRYING
            charge = transition(
                charge=charge,
                new_state=SubscriptionState.RETRYING,
                event="RETRY_ATTEMPT_STARTED",
                decision="EXECUTE_RETRY_PAYMENT",
                reason=f"Initiating payment retry attempt using Idempotency-Key: {idempotency_key}",
                idempotency_key=idempotency_key
            )

            # 4. Separate Attempt Counter Increment
            if charge.failure_reason == FailureReason.NETWORK_ERROR:
                charge.infra_attempt_count += 1
            else:
                charge.customer_attempt_count += 1

        # 5. Simulate Payment Gateway Outcome
        success = RetryExecutor._simulate_gateway_call(charge, force_outcome)

        if success:
            # Transition: RETRYING -> RECOVERED
            charge = transition(
                charge=charge,
                new_state=SubscriptionState.RECOVERED,
                event="RETRY_PAYMENT_SUCCESS",
                decision="MARK_CHARGE_RECOVERED",
                reason=f"Payment charge recovered successfully on attempt (infra: {charge.infra_attempt_count}, customer: {charge.customer_attempt_count}).",
                idempotency_key=idempotency_key
            )
            result_payload = {
                "status": "RECOVERED",
                "message": "Payment succeeded and subscription recovered.",
                "charge": charge.model_dump(mode="json")
            }
        else:
            # Transition: RETRYING -> RETRY_SCHEDULED, ESCALATED, or ABANDONED via hybrid decision engine
            decision = evaluate_hybrid_decision(charge)
            charge = transition(
                charge=charge,
                new_state=decision.next_state,
                event="RETRY_PAYMENT_FAILED",
                decision=decision.decision,
                reason=f"Retry payment failed. {decision.reason}",
                idempotency_key=idempotency_key,
                delay_seconds=decision.delay_seconds,
                next_retry_at=decision.next_retry_at,
                action_dispatch_note=decision.action_dispatch_note,
                decision_source=decision.decision_source,
                decision_source_reason=decision.decision_source_reason,
                ai_confidence=decision.ai_confidence,
                ai_reasoning=decision.ai_reasoning,
            )
            result_payload = {
                "status": charge.state.value,
                "message": f"Payment retry failed. State moved to {charge.state.value}.",
                "decision": decision.model_dump(mode="json"),
                "charge": charge.model_dump(mode="json")
            }

        # 6. Cache outcome under Idempotency Key
        storage.save_idempotent_result(idempotency_key, result_payload)

        return {
            "idempotent_replay": False,
            "idempotency_key": idempotency_key,
            "result": result_payload
        }

    @staticmethod
    def _simulate_gateway_call(charge: FailedCharge, force_outcome: Optional[str]) -> bool:
        """Simulates payment gateway retry result based on default rule or explicit override."""
        if force_outcome == "SUCCESS":
            return True
        elif force_outcome == "FAILURE":
            return False

        # Default recovery logic for simulation:
        # INSUFFICIENT_FUNDS succeeds on customer_attempt_count >= 2
        if charge.failure_reason == FailureReason.INSUFFICIENT_FUNDS and charge.customer_attempt_count >= 2:
            return True
        # NETWORK_ERROR succeeds on infra_attempt_count >= 2
        if charge.failure_reason == FailureReason.NETWORK_ERROR and charge.infra_attempt_count >= 2:
            return True

        return False
