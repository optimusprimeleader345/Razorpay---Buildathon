import sys
import os
import json
import random
from typing import List, Dict, Any
from unittest.mock import patch

# Ensure root workspace directory is in python sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from app.main import app
from app.storage import storage
from app.models import FailureReason, SubscriptionState
from app.ai_decision import AIDecisionEvaluation, AIDecisionResult

client = TestClient(app)


def generate_batch_dataset():
    """Populates 55 synthetic failed charges adhering to the specified bucket distributions."""
    print("Resetting database to pristine mock state...")
    client.post("/mock-data/reset")

    created_charges: List[Dict[str, Any]] = []

    # ----------------------------------------------------
    # Bucket 1 (~55% = 30 charges): Standard mix
    # 40% INSUFFICIENT_FUNDS (12), 25% NETWORK_ERROR (8), 25% CARD_DECLINED (7), 10% CARD_EXPIRED/INVALID (3)
    # ----------------------------------------------------
    print("\n[Bucket 1] Generating 30 standard mixed failure charges...")
    bucket1_reasons = (
        [FailureReason.INSUFFICIENT_FUNDS] * 12 +
        [FailureReason.NETWORK_ERROR] * 8 +
        [FailureReason.CARD_DECLINED] * 7 +
        [FailureReason.CARD_EXPIRED] * 2 +
        [FailureReason.INVALID_CARD_DETAILS] * 1
    )

    for i, reason in enumerate(bucket1_reasons, start=1):
        amount = round(random.uniform(100.0, 5000.0), 2)
        res = client.post("/charges/simulate", json={
            "amount": amount,
            "currency": "USD",
            "customer_id": f"cust_b1_{i:02d}",
            "failure_reason": reason.value,
            "customer_attempt_count": 0,
            "infra_attempt_count": 0,
            "state": "DETECTED"
        })
        created_charges.append({"bucket": "Bucket 1", "data": res.json()})

    # ----------------------------------------------------
    # Bucket 2 (~20% = 11 charges): Pre-set at cap boundary (2/3 customer or 4/5 infra)
    # ----------------------------------------------------
    print("[Bucket 2] Generating 11 pre-set near-cap charges (2/3 customer or 4/5 infra attempts)...")
    for i in range(1, 12):
        if i % 2 == 1:
            reason = FailureReason.INSUFFICIENT_FUNDS
            cust_cnt = 2
            infra_cnt = 0
        else:
            reason = FailureReason.NETWORK_ERROR
            cust_cnt = 0
            infra_cnt = 4

        amount = round(random.uniform(150.0, 2500.0), 2)
        res = client.post("/charges/simulate", json={
            "amount": amount,
            "currency": "USD",
            "customer_id": f"cust_b2_{i:02d}",
            "failure_reason": reason.value,
            "customer_attempt_count": cust_cnt,
            "infra_attempt_count": infra_cnt,
            "state": "RETRY_SCHEDULED"
        })
        created_charges.append({"bucket": "Bucket 2", "data": res.json()})

    # ----------------------------------------------------
    # Bucket 3 (~12% = 7 charges): Ambiguous/Borderline AI confidence telemetry
    # ----------------------------------------------------
    print("[Bucket 3] Generating 7 ambiguous/borderline AI confidence charges...")
    for i in range(1, 8):
        amount = round(random.uniform(300.0, 4000.0), 2)
        res = client.post("/charges/simulate", json={
            "amount": amount,
            "currency": "USD",
            "customer_id": f"cust_b3_ambiguous_{i:02d}",
            "failure_reason": FailureReason.CARD_DECLINED.value if i % 2 == 0 else FailureReason.INSUFFICIENT_FUNDS.value,
            "customer_attempt_count": 1,
            "infra_attempt_count": 0,
            "state": "DETECTED"
        })
        created_charges.append({"bucket": "Bucket 3", "data": res.json()})

    # ----------------------------------------------------
    # Bucket 4 (~8% = 4 charges): Exactly at cap (3/3 customer or 5/5 infra) for safety override test
    # ----------------------------------------------------
    print("[Bucket 4] Generating 4 exact-cap charges (3/3 or 5/5) to test safety override paths...")
    for i in range(1, 5):
        if i % 2 == 1:
            reason = FailureReason.INSUFFICIENT_FUNDS
            cust_cnt = 3
            infra_cnt = 0
        else:
            reason = FailureReason.NETWORK_ERROR
            cust_cnt = 0
            infra_cnt = 5

        amount = round(random.uniform(500.0, 3500.0), 2)
        res = client.post("/charges/simulate", json={
            "amount": amount,
            "currency": "USD",
            "customer_id": f"cust_b4_at_cap_{i:02d}",
            "failure_reason": reason.value,
            "customer_attempt_count": cust_cnt,
            "infra_attempt_count": infra_cnt,
            "state": "RETRY_SCHEDULED"
        })
        created_charges.append({"bucket": "Bucket 4", "data": res.json()})

    # ----------------------------------------------------
    # Bucket 5 (~5% = 3 charges): Edge cases ($1 micro, $50,000 whale, unusual ID)
    # ----------------------------------------------------
    print("[Bucket 5] Generating 3 edge-case charges ($1 micro, $58,500 whale, unusual customer ID)...")
    edge_cases = [
        {"amount": 1.00, "customer_id": "cust_MICRO_001", "reason": FailureReason.INSUFFICIENT_FUNDS},
        {"amount": 58500.00, "customer_id": "cust_WHALE_VIP_999", "reason": FailureReason.NETWORK_ERROR},
        {"amount": 1499.99, "customer_id": "cust_EDGE_#999_SPECIAL", "reason": FailureReason.CARD_DECLINED},
    ]
    for edge in edge_cases:
        res = client.post("/charges/simulate", json={
            "amount": edge["amount"],
            "currency": "USD",
            "customer_id": edge["customer_id"],
            "failure_reason": edge["reason"].value,
            "customer_attempt_count": 0,
            "infra_attempt_count": 0,
            "state": "DETECTED"
        })
        created_charges.append({"bucket": "Bucket 5", "data": res.json()})

    print(f"\nTotal synthetic charges generated: {len(created_charges)}")
    return created_charges


def process_batch_until_terminal(charges_info: List[Dict[str, Any]], max_iterations: int = 20):
    """
    Repeatedly processes charges in active states (DETECTED, RETRY_SCHEDULED) until all charges
    reach terminal states (RECOVERED, ESCALATED, ABANDONED) or max_iterations safety cap is hit.
    """
    print(f"\nProcessing batch charges repeatedly until 100% reach terminal states (Max Iterations: {max_iterations})...")
    bucket_map = {item["data"]["id"]: item["bucket"] for item in charges_info}

    for iteration in range(1, max_iterations + 1):
        charges = storage.list_charges()
        active_charges = [c for c in charges if c.state in (SubscriptionState.DETECTED, SubscriptionState.RETRY_SCHEDULED)]

        if not active_charges:
            print(f"All {len(charges)} charges reached terminal states after {iteration - 1} iterations!")
            break

        print(f"Iteration #{iteration}: {len(active_charges)} active charges remaining...")

        for charge in active_charges:
            bucket = bucket_map.get(charge.id, "Bucket 1")
            cid = charge.id

            # Mock AI behavior depending on bucket
            if bucket == "Bucket 3":
                mock_ai = AIDecisionEvaluation(
                    success=True,
                    result=AIDecisionResult(
                        recommended_action="RETRY",
                        confidence=0.52,
                        reasoning="Borderline signals; confidence below 0.70 threshold."
                    )
                )
            elif bucket == "Bucket 4":
                mock_ai = AIDecisionEvaluation(
                    success=True,
                    result=AIDecisionResult(
                        recommended_action="RETRY",
                        confidence=0.96,
                        reasoning="AI confidently suggests retrying despite attempt limit."
                    )
                )
            else:
                mock_ai = AIDecisionEvaluation(
                    success=True,
                    result=AIDecisionResult(
                        recommended_action="RETRY" if charge.failure_reason in (FailureReason.INSUFFICIENT_FUNDS, FailureReason.NETWORK_ERROR, FailureReason.CARD_DECLINED) else "ESCALATE",
                        confidence=0.88,
                        reasoning="High confidence recovery strategy."
                    )
                )

            with patch("app.decision_engine.ai_evaluate", return_value=mock_ai):
                # 1. Process if in DETECTED state
                if charge.state == SubscriptionState.DETECTED:
                    client.post(f"/charges/{cid}/process")

                # 2. Re-fetch updated charge state
                current_charge = storage.get_charge(cid)
                if current_charge and current_charge.state in (SubscriptionState.RETRY_SCHEDULED, SubscriptionState.DETECTED):
                    idemp_key = f"batch_loop_iter_{iteration}_{cid}"
                    force_payload = {"force_outcome": "FAILURE"} if bucket == "Bucket 4" else None
                    client.post(f"/charges/{cid}/retry", headers={"Idempotency-Key": idemp_key}, json=force_payload)


def main():
    charges_info = generate_batch_dataset()
    process_batch_until_terminal(charges_info, max_iterations=20)

    # Fetch final analytics summary
    print("\nFetching final GET /analytics/summary...")
    summary_res = client.get("/analytics/summary")
    summary_data = summary_res.json()

    print("\n==================================================")
    print("     FINAL TERMINAL BATCH EXECUTION RESULTS      ")
    print("==================================================")
    print(f"Total Failed Volume:            ${summary_data['total_failed_volume']:,.2f}")
    print(f"Total Recovered Volume:         ${summary_data['total_recovered_volume']:,.2f}")
    print(f"Baseline Recovery Rate (No Retries): {summary_data['baseline_recovery_rate_percentage']:.2f}% ({summary_data['recovered_on_first_attempt_count']} charges)")
    print(f"Bounded System Recovery Rate:   {summary_data['recovery_rate_percentage']:.2f}%")
    print(f"AI Decision Count:              {summary_data['ai_decision_count']}")
    print(f"Rule Fallback Count:            {summary_data['rule_fallback_count']}")
    print(f"Safety Overrides Count:         {summary_data['safety_overrides_count']}")
    print("\nFinal State Breakdown:")
    for st, count in summary_data["charges_by_state"].items():
        print(f"  - {st:<16}: {count}")
    print("\nFailure Reason Breakdown:")
    for rsn, count in summary_data["charges_by_reason"].items():
        print(f"  - {rsn:<22}: {count}")
    print("==================================================\n")

    # Save output evidence to batch_test_results.json
    output_payload = {
        "metadata": {
            "batch_size": len(charges_info),
            "status": "TERMINAL_STATE_COMPLETED",
            "buckets_distribution": {
                "bucket_1_standard": 30,
                "bucket_2_prepopulated_near_cap": 11,
                "bucket_3_ambiguous_ai_confidence": 7,
                "bucket_4_safety_override_cap": 4,
                "bucket_5_edge_cases": 3
            }
        },
        "analytics_summary": summary_data
    }

    output_path = "batch_test_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_payload, f, indent=2)

    print(f"Final batch test evidence successfully saved to '{output_path}'.")


if __name__ == "__main__":
    main()
