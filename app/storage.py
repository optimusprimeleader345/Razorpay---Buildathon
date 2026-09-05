"""In-memory data store providing mock charges, audit logs, and idempotency cache."""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from app.models import FailedCharge, AuditLogEntry, SubscriptionState, FailureReason


import threading


def get_initial_mock_charges() -> Dict[str, FailedCharge]:
    """Generates initial mock failed recurring subscription charges."""
    now = datetime.now(timezone.utc)
    return {
        "chg_101": FailedCharge(
            id="chg_101",
            amount=49.99,
            currency="USD",
            customer_id="cust_8801",
            failure_reason=FailureReason.INSUFFICIENT_FUNDS,
            customer_attempt_count=0,
            infra_attempt_count=0,
            state=SubscriptionState.DETECTED,
            timestamp=now,
            updated_at=now
        ),
        "chg_102": FailedCharge(
            id="chg_102",
            amount=199.00,
            currency="USD",
            customer_id="cust_8802",
            failure_reason=FailureReason.NETWORK_ERROR,
            customer_attempt_count=0,
            infra_attempt_count=0,
            state=SubscriptionState.DETECTED,
            timestamp=now,
            updated_at=now
        ),
        "chg_103": FailedCharge(
            id="chg_103",
            amount=29.50,
            currency="USD",
            customer_id="cust_8803",
            failure_reason=FailureReason.CARD_EXPIRED,
            customer_attempt_count=0,
            infra_attempt_count=0,
            state=SubscriptionState.DETECTED,
            timestamp=now,
            updated_at=now
        ),
        "chg_104": FailedCharge(
            id="chg_104",
            amount=99.99,
            currency="USD",
            customer_id="cust_8804",
            failure_reason=FailureReason.CARD_DECLINED,
            customer_attempt_count=0,
            infra_attempt_count=0,
            state=SubscriptionState.DETECTED,
            timestamp=now,
            updated_at=now
        ),
        "chg_105": FailedCharge(
            id="chg_105",
            amount=15.00,
            currency="USD",
            customer_id="cust_8805",
            failure_reason=FailureReason.INVALID_CARD_DETAILS,
            customer_attempt_count=0,
            infra_attempt_count=0,
            state=SubscriptionState.DETECTED,
            timestamp=now,
            updated_at=now
        ),
    }


class InMemoryStorage:
    """Thread-safe in-memory data storage engine for demonstration/testing."""

    def __init__(self):
        self._lock = threading.RLock()
        self.reset()

    def reset(self):
        """Resets the storage to pristine initial state."""
        with getattr(self, "_lock", threading.RLock()):
            self._charges: Dict[str, FailedCharge] = get_initial_mock_charges()
            self._audit_logs: Dict[str, List[AuditLogEntry]] = {charge_id: [] for charge_id in self._charges}
            self._idempotency_registry: Dict[str, Any] = {}

    def get_charge(self, charge_id: str) -> Optional[FailedCharge]:
        with self._lock:
            return self._charges.get(charge_id)

    def list_charges(self) -> List[FailedCharge]:
        with self._lock:
            return list(self._charges.values())

    def save_charge(self, charge: FailedCharge):
        with self._lock:
            self._charges[charge.id] = charge

    def add_audit_log(self, entry: AuditLogEntry):
        with self._lock:
            if entry.charge_id not in self._audit_logs:
                self._audit_logs[entry.charge_id] = []
            self._audit_logs[entry.charge_id].append(entry)

    def get_audit_logs(self, charge_id: str) -> List[AuditLogEntry]:
        with self._lock:
            return self._audit_logs.get(charge_id, [])

    def get_idempotent_result(self, key: str) -> Optional[Any]:
        with self._lock:
            return self._idempotency_registry.get(key)

    def save_idempotent_result(self, key: str, result: Any):
        with self._lock:
            self._idempotency_registry[key] = result

    def add_charge(self, charge: FailedCharge):
        """Adds a newly created charge to storage."""
        with self._lock:
            self._charges[charge.id] = charge
            if charge.id not in self._audit_logs:
                self._audit_logs[charge.id] = []

    def get_analytics_summary(self) -> dict:
        """Computes real-time financial metrics, AI stats, and safety overrides count."""
        with self._lock:
            charges = list(self._charges.values())
            all_logs: List[AuditLogEntry] = []
            for logs in self._audit_logs.values():
                all_logs.extend(logs)

            total_failed_volume = round(sum(c.amount for c in charges), 2)
            total_recovered_volume = round(sum(c.amount for c in charges if c.state == SubscriptionState.RECOVERED), 2)
            recovery_rate = round((total_recovered_volume / total_failed_volume * 100.0), 2) if total_failed_volume > 0 else 0.0

            total_charges_cnt = len(charges)
            recovered_on_first_attempt_cnt = sum(
                1 for c in charges
                if c.state == SubscriptionState.RECOVERED and (c.customer_attempt_count + c.infra_attempt_count) == 1
            )
            baseline_recovery_rate = round((recovered_on_first_attempt_cnt / total_charges_cnt * 100.0), 2) if total_charges_cnt > 0 else 0.0

            ai_count = sum(1 for log in all_logs if log.decision_source == "ai_decision")
            fallback_count = sum(1 for log in all_logs if log.decision_source == "rule_based_fallback")
            safety_overrides = sum(
                1 for log in all_logs
                if log.decision_source_reason and "AI recommended RETRY but rule engine enforced ESCALATED" in log.decision_source_reason
            )

            charges_by_state = {st.value: 0 for st in SubscriptionState}
            for c in charges:
                charges_by_state[c.state.value] = charges_by_state.get(c.state.value, 0) + 1

            charges_by_reason = {r.value: 0 for r in FailureReason}
            for c in charges:
                charges_by_reason[c.failure_reason.value] = charges_by_reason.get(c.failure_reason.value, 0) + 1

            return {
                "total_failed_volume": total_failed_volume,
                "total_recovered_volume": total_recovered_volume,
                "recovery_rate_percentage": recovery_rate,
                "baseline_recovery_rate_percentage": baseline_recovery_rate,
                "recovered_on_first_attempt_count": recovered_on_first_attempt_cnt,
                "ai_decision_count": ai_count,
                "rule_fallback_count": fallback_count,
                "safety_overrides_count": safety_overrides,
                "charges_by_state": charges_by_state,
                "charges_by_reason": charges_by_reason,
            }


# Global storage instance
storage = InMemoryStorage()
