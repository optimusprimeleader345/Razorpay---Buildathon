"""Domain models and schema definitions for Failed Subscription Recovery Agent."""

from enum import Enum
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class SubscriptionState(str, Enum):
    """Finite state machine states for subscription failure lifecycle."""
    DETECTED = "DETECTED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    RETRYING = "RETRYING"
    RECOVERED = "RECOVERED"
    ESCALATED = "ESCALATED"
    ABANDONED = "ABANDONED"


class FailureReason(str, Enum):
    """Categorized subscription payment failure reasons."""
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    CARD_EXPIRED = "CARD_EXPIRED"
    CARD_DECLINED = "CARD_DECLINED"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_CARD_DETAILS = "INVALID_CARD_DETAILS"


class FailedCharge(BaseModel):
    """Model representing a failed recurring subscription payment charge."""
    id: str
    amount: float
    currency: str = "USD"
    customer_id: str
    failure_reason: FailureReason
    customer_attempt_count: int = Field(default=0, description="Customer-fault retry attempt counter (cap: 3)")
    infra_attempt_count: int = Field(default=0, description="Infrastructure fault retry attempt counter (cap: 5)")
    state: SubscriptionState = SubscriptionState.DETECTED
    next_retry_at: Optional[datetime] = None
    next_retry_delay_seconds: Optional[int] = None
    timestamp: datetime
    updated_at: datetime

    @property
    def total_attempt_count(self) -> int:
        """Total combined attempt count across all fault categories."""
        return self.customer_attempt_count + self.infra_attempt_count


class AuditLogEntry(BaseModel):
    """Structured audit trail record logged for every state transition."""
    log_id: str
    charge_id: str
    timestamp: datetime
    event: str
    decision: str
    reason: str
    previous_state: SubscriptionState
    new_state: SubscriptionState
    idempotency_key: Optional[str] = None
    next_retry_delay_seconds: Optional[int] = None
    next_retry_at: Optional[datetime] = None
    action_dispatch_note: Optional[str] = None
    decision_source: str = Field(default="rule_based_fallback", description="'ai_decision' or 'rule_based_fallback'")
    decision_source_reason: Optional[str] = None
    ai_confidence: Optional[float] = None
    ai_reasoning: Optional[str] = None


class SimulateChargeRequest(BaseModel):
    """Request payload to dynamically inject a new simulated failed charge."""
    amount: float = Field(default=99.99, gt=0.0)
    currency: str = "USD"
    customer_id: Optional[str] = None
    failure_reason: FailureReason = FailureReason.INSUFFICIENT_FUNDS
    customer_attempt_count: int = Field(default=0, ge=0)
    infra_attempt_count: int = Field(default=0, ge=0)
    state: SubscriptionState = SubscriptionState.DETECTED


class AnalyticsSummaryResponse(BaseModel):
    """Response model for financial recovery analytics, baseline comparisons, and decision safety metrics."""
    total_failed_volume: float
    total_recovered_volume: float
    recovery_rate_percentage: float
    baseline_recovery_rate_percentage: float
    recovered_on_first_attempt_count: int
    ai_decision_count: int
    rule_fallback_count: int
    safety_overrides_count: int
    charges_by_state: dict[str, int]
    charges_by_reason: dict[str, int]
