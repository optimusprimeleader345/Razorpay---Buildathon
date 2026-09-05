"""Deterministic decision engine and hybrid AI coordinator for failed subscription recovery."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from pydantic import BaseModel, Field

from app.models import FailedCharge, SubscriptionState, FailureReason
from app.ai_decision import ai_evaluate, AI_CONFIDENCE_THRESHOLD


class DecisionResult(BaseModel):
    """Encapsulates the decision outcome for a failed subscription charge."""
    next_state: SubscriptionState
    event: str
    decision: str
    reason: str
    delay_seconds: Optional[int] = None
    next_retry_at: Optional[datetime] = None
    action_dispatch_note: Optional[str] = None
    decision_source: str = Field(default="rule_based_fallback", description="'ai_decision' or 'rule_based_fallback'")
    decision_source_reason: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_reasoning: Optional[str] = None


def calculate_backoff_delay(failure_reason: FailureReason, current_attempt: int) -> Optional[int]:
    """
    Calculates backoff retry delay in seconds based on failure reason and current attempt count.
    """
    if failure_reason == FailureReason.INSUFFICIENT_FUNDS:
        delays = [120, 300, 600]
        return delays[current_attempt] if current_attempt < len(delays) else None
    
    elif failure_reason == FailureReason.NETWORK_ERROR:
        delays = [5, 15, 30, 60, 120]
        return delays[current_attempt] if current_attempt < len(delays) else None

    elif failure_reason == FailureReason.CARD_DECLINED:
        delays = [30, 60, 120]
        return delays[current_attempt] if current_attempt < len(delays) else None

    elif failure_reason in (FailureReason.CARD_EXPIRED, FailureReason.INVALID_CARD_DETAILS):
        return None

    return None


def evaluate_next_step(charge: FailedCharge) -> DecisionResult:
    """
    Rule-based deterministic decision function evaluating the next state for a failed charge.
    """
    now = datetime.now(timezone.utc)
    escl_queue_note = "would notify support queue: subscription-recovery-escalations"

    # Rule 1: Immediate Escalation for Hard Failures
    if charge.failure_reason in (FailureReason.CARD_EXPIRED, FailureReason.INVALID_CARD_DETAILS):
        return DecisionResult(
            next_state=SubscriptionState.ESCALATED,
            event="HARD_FAILURE_DETECTED",
            decision="ESCALATE_IMMEDIATELY",
            reason=f"Failure reason '{charge.failure_reason.value}' cannot be recovered by retries without customer intervention.",
            action_dispatch_note=escl_queue_note,
            decision_source="rule_based_fallback",
            decision_source_reason="Hard failure policy enforced by rule engine"
        )

    # Rule 2: Infrastructure Fault Budget Stopping Rule (NETWORK_ERROR)
    if charge.failure_reason == FailureReason.NETWORK_ERROR:
        if charge.infra_attempt_count >= 5:
            return DecisionResult(
                next_state=SubscriptionState.ESCALATED,
                event="INFRA_RETRY_BUDGET_EXHAUSTED",
                decision="ESCALATE_INFRA_BUDGET_EXHAUSTED",
                reason=f"Infrastructure retry cap reached ({charge.infra_attempt_count}/5 attempts). Escalating for infrastructure investigation.",
                action_dispatch_note=escl_queue_note,
                decision_source="rule_based_fallback",
                decision_source_reason="Infra attempt budget exhausted (5/5)"
            )

        delay_seconds = calculate_backoff_delay(charge.failure_reason, charge.infra_attempt_count)
        next_retry = now + timedelta(seconds=delay_seconds) if delay_seconds is not None else None

        return DecisionResult(
            next_state=SubscriptionState.RETRY_SCHEDULED,
            event="INFRA_RETRY_SCHEDULED",
            decision="SCHEDULE_SHORT_INFRA_RETRY",
            reason=f"Transient infrastructure network fault detected. Scheduling retry attempt #{charge.infra_attempt_count + 1} with short delay.",
            delay_seconds=delay_seconds,
            next_retry_at=next_retry,
            decision_source="rule_based_fallback",
            decision_source_reason="Rule-based infra retry scheduling"
        )

    # Rule 3: Customer Fault Budget Stopping Rule (INSUFFICIENT_FUNDS, CARD_DECLINED)
    if charge.customer_attempt_count >= 3:
        return DecisionResult(
            next_state=SubscriptionState.ESCALATED,
            event="CUSTOMER_RETRY_BUDGET_EXHAUSTED",
            decision="ESCALATE_MAX_RETRIES_REACHED",
            reason=f"Customer retry cap reached ({charge.customer_attempt_count}/3 attempts). Escalating for manual support follow-up.",
            action_dispatch_note=escl_queue_note,
            decision_source="rule_based_fallback",
            decision_source_reason="Customer attempt budget exhausted (3/3)"
        )

    delay_seconds = calculate_backoff_delay(charge.failure_reason, charge.customer_attempt_count)
    next_retry = now + timedelta(seconds=delay_seconds) if delay_seconds is not None else None

    return DecisionResult(
        next_state=SubscriptionState.RETRY_SCHEDULED,
        event="CUSTOMER_RETRY_SCHEDULED",
        decision="SCHEDULE_CUSTOMER_RETRY",
        reason=f"Soft customer payment failure ('{charge.failure_reason.value}'). Scheduling retry attempt #{charge.customer_attempt_count + 1} with backoff delay.",
        delay_seconds=delay_seconds,
        next_retry_at=next_retry,
        decision_source="rule_based_fallback",
        decision_source_reason="Rule-based customer retry scheduling"
    )


def evaluate_hybrid_decision(charge: FailedCharge) -> DecisionResult:
    """
    Hybrid decision engine:
    1. Evaluates deterministic rule engine as the baseline & safety boundary.
    2. Calls AI decision layer (ai_evaluate).
    3. Checks confidence threshold (>= 0.70) and safety constraint matching.
    4. If AI passes confidence & safety constraints, uses AI decision (decision_source='ai_decision').
    5. Else falls back gracefully to rule-based engine (decision_source='rule_based_fallback').
    """
    rule_decision = evaluate_next_step(charge)
    ai_eval = ai_evaluate(charge)

    # 1. Fallback if LLM call failed, timed out, or response invalid
    if not ai_eval.success:
        rule_decision.decision_source = "rule_based_fallback"
        rule_decision.decision_source_reason = ai_eval.error_message
        return rule_decision

    ai_res = ai_eval.result

    # 2. Fallback if confidence < threshold
    if ai_res.confidence < AI_CONFIDENCE_THRESHOLD:
        rule_decision.decision_source = "rule_based_fallback"
        rule_decision.decision_source_reason = (
            f"low confidence: {ai_res.confidence:.2f} (threshold: {AI_CONFIDENCE_THRESHOLD})"
        )
        rule_decision.ai_confidence = ai_res.confidence
        rule_decision.ai_reasoning = ai_res.reasoning
        return rule_decision

    # 3. Safety Boundary Enforcement: AI cannot override hard stopping rules
    if rule_decision.next_state == SubscriptionState.ESCALATED and ai_res.recommended_action == "RETRY":
        rule_decision.decision_source = "rule_based_fallback"
        rule_decision.decision_source_reason = (
            f"AI recommended RETRY but rule engine enforced ESCALATED safety boundary ({rule_decision.reason})"
        )
        rule_decision.ai_confidence = ai_res.confidence
        rule_decision.ai_reasoning = ai_res.reasoning
        return rule_decision

    # 4. Valid AI Decision accepted
    now = datetime.now(timezone.utc)
    escl_note = "would notify support queue: subscription-recovery-escalations"

    if ai_res.recommended_action == "RETRY":
        attempt_cnt = charge.infra_attempt_count if charge.failure_reason == FailureReason.NETWORK_ERROR else charge.customer_attempt_count
        delay = calculate_backoff_delay(charge.failure_reason, attempt_cnt)
        next_retry = now + timedelta(seconds=delay) if delay is not None else None

        return DecisionResult(
            next_state=SubscriptionState.RETRY_SCHEDULED,
            event="AI_RETRY_RECOMMENDED",
            decision="AI_DECISION_SCHEDULE_RETRY",
            reason=f"AI agent recommended RETRY: {ai_res.reasoning}",
            delay_seconds=delay,
            next_retry_at=next_retry,
            decision_source="ai_decision",
            decision_source_reason=f"AI decision accepted with confidence {ai_res.confidence:.2f}",
            ai_confidence=ai_res.confidence,
            ai_reasoning=ai_res.reasoning
        )

    elif ai_res.recommended_action == "ESCALATE":
        return DecisionResult(
            next_state=SubscriptionState.ESCALATED,
            event="AI_ESCALATION_RECOMMENDED",
            decision="AI_DECISION_ESCALATE",
            reason=f"AI agent recommended ESCALATE: {ai_res.reasoning}",
            action_dispatch_note=escl_note,
            decision_source="ai_decision",
            decision_source_reason=f"AI decision accepted with confidence {ai_res.confidence:.2f}",
            ai_confidence=ai_res.confidence,
            ai_reasoning=ai_res.reasoning
        )

    elif ai_res.recommended_action == "ABANDON":
        return DecisionResult(
            next_state=SubscriptionState.ABANDONED,
            event="AI_ABANDON_RECOMMENDED",
            decision="AI_DECISION_ABANDON",
            reason=f"AI agent recommended ABANDON: {ai_res.reasoning}",
            decision_source="ai_decision",
            decision_source_reason=f"AI decision accepted with confidence {ai_res.confidence:.2f}",
            ai_confidence=ai_res.confidence,
            ai_reasoning=ai_res.reasoning
        )

    return rule_decision
