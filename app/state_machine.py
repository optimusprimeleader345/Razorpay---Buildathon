"""Finite State Machine enforcing valid state transitions and audit logging."""

import uuid
from datetime import datetime, timezone
from typing import Dict, Set, Optional

from app.models import FailedCharge, SubscriptionState, AuditLogEntry
from app.storage import storage


class InvalidStateTransitionException(ValueError):
    """Raised when an illegal or unsupported state transition is attempted."""
    pass


# Strict FSM transition map defining legal next states for each state
ALLOWED_TRANSITIONS: Dict[SubscriptionState, Set[SubscriptionState]] = {
    SubscriptionState.DETECTED: {
        SubscriptionState.RETRY_SCHEDULED,
        SubscriptionState.RETRYING,
        SubscriptionState.ESCALATED,
        SubscriptionState.ABANDONED,
    },
    SubscriptionState.RETRY_SCHEDULED: {
        SubscriptionState.RETRYING,
        SubscriptionState.ESCALATED,
        SubscriptionState.ABANDONED,
    },
    SubscriptionState.RETRYING: {
        SubscriptionState.RECOVERED,
        SubscriptionState.RETRY_SCHEDULED,
        SubscriptionState.ESCALATED,
        SubscriptionState.ABANDONED,
    },
    # Terminal states allow no further transitions
    SubscriptionState.RECOVERED: set(),
    SubscriptionState.ESCALATED: set(),
    SubscriptionState.ABANDONED: set(),
}


def transition(
    charge: FailedCharge,
    new_state: SubscriptionState,
    event: str,
    decision: str,
    reason: str,
    idempotency_key: Optional[str] = None,
    delay_seconds: Optional[int] = None,
    next_retry_at: Optional[datetime] = None,
    action_dispatch_note: Optional[str] = None,
    decision_source: str = "rule_based_fallback",
    decision_source_reason: Optional[str] = None,
    ai_confidence: Optional[float] = None,
    ai_reasoning: Optional[str] = None,
) -> FailedCharge:
    """
    Validates and executes a state transition on a FailedCharge, updating timestamps,
    storing the charge state, and logging a structured audit record.
    """
    previous_state = charge.state
    allowed = ALLOWED_TRANSITIONS.get(previous_state, set())

    if new_state not in allowed:
        raise InvalidStateTransitionException(
            f"Invalid state transition for charge '{charge.id}': cannot transition from {previous_state.value} to {new_state.value}."
        )

    now = datetime.now(timezone.utc)
    charge.state = new_state
    charge.updated_at = now
    charge.next_retry_delay_seconds = delay_seconds
    charge.next_retry_at = next_retry_at

    # Persist updated charge
    storage.save_charge(charge)

    # Write structured audit log entry
    log_entry = AuditLogEntry(
        log_id=f"log_{uuid.uuid4().hex[:12]}",
        charge_id=charge.id,
        timestamp=now,
        event=event,
        decision=decision,
        reason=reason,
        previous_state=previous_state,
        new_state=new_state,
        idempotency_key=idempotency_key,
        next_retry_delay_seconds=delay_seconds,
        next_retry_at=next_retry_at,
        action_dispatch_note=action_dispatch_note,
        decision_source=decision_source,
        decision_source_reason=decision_source_reason,
        ai_confidence=ai_confidence,
        ai_reasoning=ai_reasoning,
    )
    storage.add_audit_log(log_entry)

    return charge
