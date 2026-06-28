"""
Component 2 — Bedrock LLM Decision Engine.
Calls AWS Bedrock (Claude 3 Haiku) for runbook selection.
Fallback logic has been extracted to fallback_engine.py.
This module handles ONLY the LLM invocation path.
"""
import boto3
import json
from typing import Dict, Any, List
from app.core.config import settings
from .prompts.system_prompt import SYSTEM_PROMPT
import logging

logger = logging.getLogger(__name__)


class BedrockDecisionEngine:
    """
    LLM-powered decision engine using AWS Bedrock Claude 3 Haiku.
    Constrained to select from approved runbooks only.
    """

    def __init__(self):
        try:
            self.client = boto3.client(
                'bedrock-runtime',
                region_name=settings.BEDROCK_REGION,
            )
        except Exception as e:
            logger.warning(f"Could not initialize Bedrock client: {e}")
            self.client = None

        self.model_id = 'anthropic.claude-3-haiku-20240307-v1:0'
        self.timeout_ms = settings.BEDROCK_TIMEOUT_MS

    def decide(
        self, anomaly_context: Dict[str, Any], runbooks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Calls AWS Bedrock Claude to evaluate the anomaly and return an action plan JSON.
        Raises an exception on any failure (caught by DecisionRouter for fallback).
        """
        if not self.client:
            raise RuntimeError("AWS Bedrock client not initialized.")

        user_prompt = f"""
Anomaly Context:
{json.dumps(anomaly_context, indent=2)}

Available Runbooks:
{json.dumps(runbooks, indent=2)}
"""

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "system": SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": user_prompt,
                }
            ],
            "temperature": 0.0,
        }

        try:
            response = self.client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(body),
            )
            response_body = json.loads(response.get('body').read())
            response_text = response_body.get('content')[0].get('text')

            # Parse JSON from Claude's response
            decision = json.loads(response_text)
            logger.info(f"Bedrock LLM returned decision: {decision.get('matched_runbook', 'N/A')}")
            return decision

        except json.JSONDecodeError as e:
            # Condition 4: LLM response parse failure
            logger.error(f"Bedrock LLM returned invalid JSON: {e}")
            raise
        except Exception as e:
            # Conditions 2 & 3: timeout, 429, 5xx, connection error
            logger.error(f"Bedrock invocation failed: {e}")
            raise
