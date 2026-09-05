"""AI decision layer leveraging LLM for subscription recovery classification."""

import json
import os
from datetime import datetime, timezone
from typing import Optional, Literal
from pydantic import BaseModel, Field, ValidationError

from app.models import FailedCharge

# Named constant for AI decision confidence threshold
AI_CONFIDENCE_THRESHOLD: float = 0.70


class AIDecisionResult(BaseModel):
    """Structured response schema expected from the LLM decision layer."""
    recommended_action: Literal["RETRY", "ESCALATE", "ABANDON"]
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(description="One sentence explanation for the decision")


class AIDecisionEvaluation(BaseModel):
    """Encapsulates outcome of an AI evaluation attempt including error details."""
    success: bool
    result: Optional[AIDecisionResult] = None
    error_message: Optional[str] = None


def build_telemetry_prompt(charge: FailedCharge) -> str:
    """Constructs prompt incorporating customer telemetry and failure metrics."""
    time_since_detection = int((datetime.now(timezone.utc) - charge.timestamp).total_seconds())
    return f"""You are an AI Subscription Recovery Agent for a fintech platform.
Analyze the following failed recurring subscription charge and recommend an action.

[CHARGE TELEMETRY]
- Charge ID: {charge.id}
- Amount: {charge.amount} {charge.currency}
- Customer ID: {charge.customer_id}
- Failure Reason: {charge.failure_reason.value}
- Customer Attempt Count: {charge.customer_attempt_count} (Cap: 3)
- Infra Attempt Count: {charge.infra_attempt_count} (Cap: 5)
- Total Attempts: {charge.total_attempt_count}
- Time Since First Detection: {time_since_detection} seconds

[RULES & BOUNDARIES]
1. HARD FAILURES ('CARD_EXPIRED', 'INVALID_CARD_DETAILS') must be ESCALATE or ABANDON.
2. CUSTOMER FAULTS ('INSUFFICIENT_FUNDS', 'CARD_DECLINED') at 3 attempts must be ESCALATE.
3. INFRA FAULTS ('NETWORK_ERROR') at 5 attempts must be ESCALATE.

Respond ONLY with a valid JSON object matching this exact structure:
{{
  "recommended_action": "RETRY" | "ESCALATE" | "ABANDON",
  "confidence": 0.85,
  "reasoning": "One sentence explanation of why this action was selected."
}}
Do NOT include markdown wrapping or extraneous prose.
"""


def ai_evaluate(charge: FailedCharge) -> AIDecisionEvaluation:
    """
    Evaluates a failed charge using Anthropic LLM (claude-sonnet-4-6).
    Returns AIDecisionEvaluation. If parsing fails, API key is missing, or LLM call fails,
    returns success=False with explicit error_message.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return AIDecisionEvaluation(
            success=False,
            error_message="LLM call failed: ANTHROPIC_API_KEY environment variable not set"
        )

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        prompt = build_telemetry_prompt(charge)

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}]
        )

        content_text = response.content[0].text.strip()
        # Clean any potential markdown block wrappers
        if content_text.startswith("```"):
            content_text = content_text.strip("`").replace("json\n", "").strip()

        data = json.loads(content_text)
        validated_result = AIDecisionResult(**data)

        return AIDecisionEvaluation(
            success=True,
            result=validated_result
        )

    except json.JSONDecodeError as err:
        return AIDecisionEvaluation(
            success=False,
            error_message=f"LLM call failed: Invalid JSON response structure ({str(err)})"
        )
    except ValidationError as err:
        return AIDecisionEvaluation(
            success=False,
            error_message=f"LLM call failed: Response failed Pydantic schema validation ({str(err)})"
        )
    except Exception as err:
        return AIDecisionEvaluation(
            success=False,
            error_message=f"LLM call failed: {str(err)}"
        )
