from dotenv import load_dotenv
load_dotenv()

import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Header, Query, status
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.models import (
    FailedCharge,
    AuditLogEntry,
    SubscriptionState,
    SimulateChargeRequest,
    AnalyticsSummaryResponse
)
from app.storage import storage
from app.decision_engine import evaluate_next_step, evaluate_hybrid_decision
from app.state_machine import transition, InvalidStateTransitionException
from app.executor import RetryExecutor

app = FastAPI(
    title="Failed Subscription Recovery Agent API",
    description="Deterministic rule-based agent scaffolding with FSM, idempotency, backoff delays, and audit trails.",
    version="0.1.0"
)

# Serve Razorpay-branded landing page & interactive UI
app.mount("/static", StaticFiles(directory="app/static"), name="static")


class RetryRequest(BaseModel):
    force_outcome: Optional[str] = None  # "SUCCESS" or "FAILURE" for demo simulation overrides



@app.get("/", response_class=FileResponse)
def serve_landing_page():
    """Serves the Razorpay Recovery Agent landing page & interactive UI."""
    return FileResponse("app/static/index.html")


@app.get("/api/health")
def api_health():
    """System health check and metadata."""
    return {
        "agent": "Failed Subscription Recovery Agent",
        "status": "online",
        "docs_url": "/docs",
        "description": "Hybrid AI + FSM state machine engine."
    }


@app.get("/charges", response_model=List[FailedCharge])
def list_charges():
    """Retrieves all tracked failed subscription charges."""
    return storage.list_charges()


@app.get("/charges/{charge_id}", response_model=FailedCharge)
def get_charge(charge_id: str):
    """Retrieves single charge details by ID."""
    charge = storage.get_charge(charge_id)
    if not charge:
        raise HTTPException(status_code=404, detail=f"Charge '{charge_id}' not found.")
    return charge


@app.post("/charges/{charge_id}/process")
def process_detected_charge(charge_id: str):
    """
    Evaluates a DETECTED charge using the hybrid decision engine and transitions it to RETRY_SCHEDULED or ESCALATED.
    """
    with storage._lock:
        charge = storage.get_charge(charge_id)
        if not charge:
            raise HTTPException(status_code=404, detail=f"Charge '{charge_id}' not found.")

        if charge.state != SubscriptionState.DETECTED:
            raise HTTPException(
                status_code=400,
                detail=f"Charge '{charge_id}' is in state '{charge.state.value}'. Only DETECTED charges can be processed."
            )

        decision = evaluate_hybrid_decision(charge)
        updated_charge = transition(
            charge=charge,
            new_state=decision.next_state,
            event=decision.event,
            decision=decision.decision,
            reason=decision.reason,
            delay_seconds=decision.delay_seconds,
            next_retry_at=decision.next_retry_at,
            action_dispatch_note=decision.action_dispatch_note,
            decision_source=decision.decision_source,
            decision_source_reason=decision.decision_source_reason,
            ai_confidence=decision.ai_confidence,
            ai_reasoning=decision.ai_reasoning,
        )

        return {
            "message": f"Charge processed successfully. Moved to state {updated_charge.state.value}.",
            "decision": decision,
            "charge": updated_charge
        }


@app.post("/charges/{charge_id}/retry")
def retry_charge(
    charge_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key", description="Unique idempotency key for this attempt"),
    body: Optional[RetryRequest] = None
):
    """
    Executes a retry attempt for a charge. Requires an Idempotency-Key header to prevent duplicate execution.
    """
    force_outcome = body.force_outcome if body else None
    try:
        result = RetryExecutor.execute_retry(
            charge_id=charge_id,
            idempotency_key=idempotency_key,
            force_outcome=force_outcome
        )
        return result
    except InvalidStateTransitionException as err:
        raise HTTPException(status_code=400, detail=str(err))
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))


@app.get("/charges/{charge_id}/logs", response_model=List[AuditLogEntry])
def get_charge_audit_logs(charge_id: str):
    """Retrieves structured audit log history for a specific charge."""
    charge = storage.get_charge(charge_id)
    if not charge:
        raise HTTPException(status_code=404, detail=f"Charge '{charge_id}' not found.")
    return storage.get_audit_logs(charge_id)





@app.get("/analytics/summary", response_model=AnalyticsSummaryResponse)
def get_analytics_summary():
    """
    Retrieves real-time recovery analytics, total failed vs. recovered revenue volume,
    AI vs. rule-based decision counts, and the safety overrides count.
    """
    return storage.get_analytics_summary()


@app.post("/charges/simulate", response_model=FailedCharge)
def simulate_new_charge(body: Optional[SimulateChargeRequest] = None):
    """
    Dynamically injects a new simulated failed charge into the storage.
    Allows setting starting attempt counts (e.g. 2/3 attempts) for fast demo walkthroughs.
    """
    req = body or SimulateChargeRequest()
    now = datetime.now(timezone.utc)
    charge_id = f"chg_sim_{uuid.uuid4().hex[:8]}"
    cust_id = req.customer_id or f"cust_sim_{uuid.uuid4().hex[:6]}"

    charge = FailedCharge(
        id=charge_id,
        amount=req.amount,
        currency=req.currency,
        customer_id=cust_id,
        failure_reason=req.failure_reason,
        customer_attempt_count=req.customer_attempt_count,
        infra_attempt_count=req.infra_attempt_count,
        state=req.state,
        timestamp=now,
        updated_at=now
    )
    storage.add_charge(charge)
    return charge


@app.post("/mock-data/reset")
def reset_mock_data():
    """Resets the in-memory database, logs, and idempotency registry to pristine mock state."""
    storage.reset()
    return {"message": "Mock dataset successfully reset to initial pristine state."}
