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
        Falls back to rule-based decision if Bedrock is unavailable (per AI API Contract §4 Fallback).
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
        """
        Rule-based fallback per AI API Contract §4 — Fallback Rule-Based conditions.
        Maps suspected_fault_type to the most appropriate runbook and action.
        """
        fault_type = anomaly_context.get("suspected_fault_type", "unknown")
        target_service = anomaly_context.get("deployment", anomaly_context.get("target_service", "unknown"))
        namespace = anomaly_context.get("namespace", "production")
        container = "main"  # Default container name matching demo output

        # Smart fault-type → runbook mapping based on runbooks.json signal_triggers
        fault_to_runbook = {
            "database_connection_failure": {
                "runbook": "DatabaseConnectionRecoveryRunbook",
                "pattern_type": "urgent",
                "action": "PATCH_MEMORY_LIMIT",
                "params": {
                    "namespace": namespace,
                    "container": container,
                    "memory_request_mb": 512,
                    "memory_limit_mb": 768
                }
            },
            "service_error_spike": {
                "runbook": "DatabaseConnectionRecoveryRunbook",
                "pattern_type": "urgent",
                "action": "RESTART_DEPLOYMENT",
                "params": {
                    "namespace": namespace,
                    "grace_period_seconds": 30
                }
            },
            "pod_oom_killed": {
                "runbook": "PodOOMKilledRunbook",
                "pattern_type": "urgent",
                "action": "PATCH_MEMORY_LIMIT",
                "params": {
                    "namespace": namespace,
                    "container": container,
                    "memory_request_mb": 512,
                    "memory_limit_mb": 1024
                }
            },
            "memory_pressure": {
                "runbook": "PodOOMKilledRunbook",
                "pattern_type": "urgent",
                "action": "PATCH_MEMORY_LIMIT",
                "params": {
                    "namespace": namespace,
                    "container": container,
                    "memory_request_mb": 512,
                    "memory_limit_mb": 1024
                }
            },
            "service_health_check_failure": {
                "runbook": "ServiceUnhealthyRunbook",
                "pattern_type": "urgent",
                "action": "RESTART_DEPLOYMENT",
                "params": {
                    "namespace": namespace,
                    "grace_period_seconds": 30
                }
            },
            "crash_loop_backoff": {
                "runbook": "ServiceUnhealthyRunbook",
                "pattern_type": "urgent",
                "action": "ROLLOUT_UNDO",
                "params": {
                    "namespace": namespace
                }
            },
            "queue_congestion": {
                "runbook": "QueueBacklogRunbook",
                "pattern_type": "deferred",
                "action": "SCALE_REPLICAS",
                "params": {
                    "namespace": namespace,
                    "replicas": 3
                }
            },
            "certificate_expiring": {
                "runbook": "CertExpiryRotationRunbook",
                "pattern_type": "deferred",
                "action": "ROTATE_SECRET",
                "params": {
                    "namespace": namespace,
                    "secret_name": f"tf-3/{target_service}/cert"
                }
            },
        }

        # Get matched config or use safe default
        match = fault_to_runbook.get(fault_type, {
            "runbook": "DatabaseConnectionRecoveryRunbook",
            "pattern_type": "urgent",
            "action": "PATCH_MEMORY_LIMIT",
            "params": {
                "namespace": namespace,
                "container": container,
                "memory_request_mb": 512,
                "memory_limit_mb": 768
            }
        })

        return {
            "matched_runbook": match["runbook"],
            "pattern_type": match["pattern_type"],
            "action_plan": [
                {
                    "step": 1,
                    "action": match["action"],
                    "target": f"deployment/{target_service}",
                    "params": match["params"]
                }
            ]
        }
