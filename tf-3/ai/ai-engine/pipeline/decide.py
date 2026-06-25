import json
import boto3
from pipeline.base import BaseDecider
from config.settings import Settings
from utils.logger import get_logger
from utils.schema_validator import DECIDE_RESPONSE_SCHEMA, validate_json

logger = get_logger("AIOpsDecider")

class RuleBasedDecider(BaseDecider):
    """
    Rule-Based implementation of BaseDecider.
    Uses predefined decision rules to map anomaly context to action plans,
    ensuring 100% offline reliability.
    """

    def __init__(self, settings: Settings = None):
        self.settings = settings or Settings()

    def decide(self, anomaly_context: dict, correlation_id: str, idempotency_key: str, dry_run_mode: bool) -> dict:
        """
        Returns a DecideResponse based on static rules.
        """
        target_service = anomaly_context["target_service"]
        fault_type = anomaly_context["suspected_fault_type"]
        namespace = anomaly_context.get("namespace", "production")
        deployment = anomaly_context.get("deployment", f"deployment/{target_service}")

        # Map fault types to runbooks and actions
        if fault_type == "cpu":
            matched_runbook = "CPUOverloadRecoveryRunbook"
            pattern_type = "urgent"
            action_plan = [
                {
                    "step": 1,
                    "action": "SCALE_REPLICAS",
                    "target": deployment,
                    "params": {
                        "namespace": namespace,
                        "replicas": 3
                    }
                }
            ]
        elif fault_type == "mem":
            matched_runbook = "MemoryLeakRecoveryRunbook"
            pattern_type = "urgent"
            action_plan = [
                {
                    "step": 1,
                    "action": "PATCH_MEMORY_LIMIT",
                    "target": deployment,
                    "params": {
                        "namespace": namespace,
                        "container": "main",
                        "memory_request_mb": 512,
                        "memory_limit_mb": 1024
                    }
                }
            ]
        elif fault_type == "crash":
            matched_runbook = "CrashLoopBackOffRecoveryRunbook"
            pattern_type = "urgent"
            action_plan = [
                {
                    "step": 1,
                    "action": "ROLLOUT_UNDO",
                    "target": deployment,
                    "params": {
                        "namespace": namespace
                    }
                }
            ]
        elif fault_type == "secret":
            matched_runbook = "SecretRotationRunbook"
            pattern_type = "deferred"
            action_plan = [
                {
                    "step": 1,
                    "action": "ROTATE_SECRET",
                    "target": deployment,
                    "params": {
                        "namespace": namespace,
                        "secret_name": f"tf-3/{target_service}/secret"
                    }
                }
            ]
        else:
            # Default fallback for network loss, delay, socket, disk, or unknown faults
            matched_runbook = "GenericServiceRestartRunbook"
            pattern_type = "urgent"
            action_plan = [
                {
                    "step": 1,
                    "action": "RESTART_DEPLOYMENT",
                    "target": deployment,
                    "params": {
                        "namespace": namespace,
                        "grace_period_seconds": 30
                    }
                }
            ]

        # Standard safety configs
        blast_radius_config = {
            "max_pod_impact_pct": 25,
            "circuit_breaker_error_rate": 0.20,
            "allowed_namespaces": [namespace]
        }

        verify_policy = {
            "window_seconds": 120,
            "success_conditions": [
                "pod_ready == true",
                "restart_count_no_increase == true",
                "container_memory_usage_pct < 80"
            ]
        }

        response = {
            "matched_runbook": matched_runbook,
            "pattern_type": pattern_type,
            "action_plan": action_plan,
            "blast_radius_config": blast_radius_config,
            "verify_policy": verify_policy,
            "correlation_id": correlation_id,
            "idempotency_key": idempotency_key,
            "dry_run_mode": dry_run_mode,
            "cost_cap_exceeded": False
        }

        validate_json(response, DECIDE_RESPONSE_SCHEMA)
        return response


class LLMDecider(BaseDecider):
    """
    LLM-Based implementation of BaseDecider using AWS Bedrock (Claude 3 Haiku).
    Includes safety gates and automatically falls back to Rule-Based decider on error.
    """

    def __init__(self, settings: Settings = None):
        self.settings = settings or Settings()
        self.rule_based_fallback = RuleBasedDecider(self.settings)
        
        # Initialize Bedrock Client safely
        try:
            self.bedrock_client = boto3.client(
                service_name="bedrock-runtime",
                region_name=self.settings.AWS_REGION
            )
        except Exception as e:
            logger.warning(f"Could not initialize AWS Bedrock client: {e}. Will use Rule-Based fallback.")
            self.bedrock_client = None

    def decide(self, anomaly_context: dict, correlation_id: str, idempotency_key: str, dry_run_mode: bool) -> dict:
        """
        Attempts to call AWS Bedrock to generate a DecideResponse.
        Falls back to Rule-Based if Bedrock fails or if cost cap is exceeded.
        """
        # 1. Check if Bedrock is disabled or client is not initialized
        if self.settings.DECIDER_TYPE == "rule-based" or not self.bedrock_client:
            logger.info("Using Rule-Based Decider (configured type or client uninitialized).")
            return self.rule_based_fallback.decide(anomaly_context, correlation_id, idempotency_key, dry_run_mode)

        # 2. Build system and user prompts
        system_prompt = (
            "Role: Bạn là chuyên gia AIOps Core Engine của hệ thống tự chữa lành đa thuê bao. "
            "Nhiệm vụ của bạn là nhận thông tin bất thường và đối chiếu với danh sách Runbook để đưa ra kế hoạch hành động tối ưu dưới dạng JSON.\n\n"
            "Safety Rules:\n"
            "1. Tuyệt đối không thực hiện các hành động nằm ngoài danh sách Runbook cho phép (RESTART_DEPLOYMENT, PATCH_MEMORY_LIMIT, SCALE_REPLICAS, ROLLOUT_UNDO, ROTATE_SECRET).\n"
            "2. Không sử dụng các từ ngữ tự do ngoài cấu trúc JSON Schema được yêu cầu.\n"
            "3. Mọi quyết định phải đi kèm giải thích ngắn gọn trong trường reasoning (tối đa 300 ký tự).\n\n"
            "Output Format: Đầu ra bắt buộc phải là một đối tượng JSON hợp lệ, tuân thủ hoàn toàn theo schema DecideResponse. Không kèm theo bất kỳ văn bản giải thích nào ngoài khối JSON."
        )

        user_prompt = f"""
Thông tin yêu cầu:
- Correlation ID: {correlation_id}
- Ngữ cảnh lỗi phát hiện:
  + Dịch vụ đích: {anomaly_context['target_service']}
  + Loại lỗi nghi ngờ: {anomaly_context['suspected_fault_type']}
  + Metric kích hoạt: {anomaly_context.get('trigger_metric', 'N/A')} (Giá trị: {anomaly_context.get('trigger_value', 0.0)})

Danh mục Runbook khả dụng:
1. CPUOverloadRecoveryRunbook: Khắc phục lỗi quá tải CPU. Hành động: SCALE_REPLICAS.
2. MemoryLeakRecoveryRunbook: Khắc phục lỗi rò rỉ bộ nhớ hoặc OOM. Hành động: PATCH_MEMORY_LIMIT.
3. CrashLoopBackOffRecoveryRunbook: Khắc phục lỗi container crash. Hành động: ROLLOUT_UNDO.
4. SecretRotationRunbook: Khắc phục hết hạn secrets. Hành động: ROTATE_SECRET.
5. GenericServiceRestartRunbook: Khắc phục lỗi mạng (loss, delay) hoặc socket. Hành động: RESTART_DEPLOYMENT.

Hãy phân tích và trả về đối tượng JSON khớp hoàn hảo với schema DecideResponse.
"""

        # 3. Invoke Bedrock
        try:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1000,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.0  # Force deterministic output
            })

            logger.info(f"Calling Bedrock model {self.settings.BEDROCK_MODEL_ID}...")
            response = self.bedrock_client.invoke_model(
                modelId=self.settings.BEDROCK_MODEL_ID,
                body=body
            )
            
            response_body = json.loads(response.get("body").read())
            text_output = response_body["content"][0]["text"]
            
            # Parse the JSON response
            # Find the start and end of JSON block if model wrapped it
            start_idx = text_output.find("{")
            end_idx = text_output.rfind("}")
            if start_idx != -1 and end_idx != -1:
                json_str = text_output[start_idx:end_idx+1]
            else:
                json_str = text_output
            
            decide_data = json.loads(json_str)
            
            # Enrich fields that must match request
            decide_data["correlation_id"] = correlation_id
            decide_data["idempotency_key"] = idempotency_key
            decide_data["dry_run_mode"] = dry_run_mode
            
            # Validate against schema
            validate_json(decide_data, DECIDE_RESPONSE_SCHEMA)
            logger.info("Successfully received and validated DecideResponse from Bedrock.")
            return decide_data

        except Exception as e:
            logger.warning(f"Bedrock invocation or validation failed: {e}. Falling back to Rule-Based decider.")
            # Return rule-based decision, but mark fallback in logs
            fallback_res = self.rule_based_fallback.decide(anomaly_context, correlation_id, idempotency_key, dry_run_mode)
            # In case of fallback because of LLM issue, cost_cap_exceeded can be set if budget limits are reached,
            # or we just return the safe fallback response.
            return fallback_res
