import boto3
import json
from typing import Dict, Any, List
from app.core.config import settings
from .prompts.system_prompt import SYSTEM_PROMPT
import logging

logger = logging.getLogger(__name__)

class BedrockDecisionEngine:
    def __init__(self):
        # We assume the environment is set up with AWS credentials (IRSA in EKS)
        try:
            self.client = boto3.client('bedrock-runtime', region_name=settings.BEDROCK_REGION)
        except Exception as e:
            logger.warning(f"Could not initialize bedrock client: {e}")
            self.client = None
            
        self.model_id = 'anthropic.claude-3-haiku-20240307-v1:0'

    def decide(self, anomaly_context: Dict[str, Any], runbooks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calls AWS Bedrock (Claude 3 Haiku) to evaluate the anomaly and return an action plan JSON.
        """
        if not self.client:
            logger.warning("AWS Bedrock client not available. Returning fallback JSON.")
            return self._fallback_decision(anomaly_context, runbooks)

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
                    "content": user_prompt
                }
            ],
            "temperature": 0.0
        }

        try:
            response = self.client.invoke_model(
                modelId=self.model_id,
                body=json.dumps(body)
            )
            response_body = json.loads(response.get('body').read())
            response_text = response_body.get('content')[0].get('text')
            
            # Safely parse JSON from Claude's output
            return json.loads(response_text)
            
        except Exception as e:
            logger.error(f"Bedrock invocation failed: {e}")
            return self._fallback_decision(anomaly_context, runbooks)

    def _fallback_decision(self, anomaly_context: Dict[str, Any], runbooks: List[Dict[str, Any]]) -> Dict[str, Any]:
        target = anomaly_context.get("deployment", "unknown")
        namespace = anomaly_context.get("namespace", "default")
        return {
            "matched_runbook": "DatabaseConnectionRecoveryRunbook",
            "pattern_type": "urgent",
            "action_plan": [
                {
                    "step": 1,
                    "action": "PATCH_MEMORY_LIMIT",
                    "target": f"deployment/{target}",
                    "params": {
                        "namespace": namespace,
                        "memory_request_mb": 512,
                        "memory_limit_mb": 768
                    }
                }
            ]
        }
