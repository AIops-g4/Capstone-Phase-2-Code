import json
import time
from typing import Optional

from src.config import (
    AWS_REGION,
    BEDROCK_DAILY_CAP_USD,
    BEDROCK_MODEL_ID,
    DECIDER_TYPE,
    RUNBOOKS_PATH,
)
from src.rule_decider import RuleBasedDecider


class CostGuard:
  """In-memory Bedrock daily spend tracker (contract cost cap fallback)."""

  def __init__(self, daily_cap_usd: float):
      self.daily_cap_usd = daily_cap_usd
      self._day = time.strftime("%Y-%m-%d", time.gmtime())
      self._spent_usd = 0.0

  def record_call(self, cost_usd: float = 0.0015) -> None:
      today = time.strftime("%Y-%m-%d", time.gmtime())
      if today != self._day:
          self._day = today
          self._spent_usd = 0.0
      self._spent_usd += cost_usd

  def is_cap_exceeded(self) -> bool:
      today = time.strftime("%Y-%m-%d", time.gmtime())
      if today != self._day:
          self._day = today
          self._spent_usd = 0.0
      return self._spent_usd >= self.daily_cap_usd


class LLMDecider:
    """Optional Bedrock decider; falls back to rule-based on error or cost cap."""

    def __init__(self):
        self.rule_fallback = RuleBasedDecider(RUNBOOKS_PATH)
        self.cost_guard = CostGuard(BEDROCK_DAILY_CAP_USD)
        self.bedrock_client = None
        try:
            import boto3

            self.bedrock_client = boto3.client(
                "bedrock-runtime", region_name=AWS_REGION
            )
        except Exception:
            self.bedrock_client = None

    def decide(self, anomaly_context: dict) -> dict:
        if DECIDER_TYPE == "rule-based" or not self.bedrock_client:
            return self.rule_fallback.decide(anomaly_context)

        if self.cost_guard.is_cap_exceeded():
            result = self.rule_fallback.decide(anomaly_context)
            result["cost_cap_exceeded"] = True
            return result

        catalog = _format_runbook_catalog(self.rule_fallback.runbooks)
        prompt = json.dumps({"anomaly_context": anomaly_context}, indent=2)
        try:
            body = json.dumps(
                {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1200,
                    "system": (
                        "Role: TF3 decide engine. Pick a PREDEFINED runbook only. "
                        "Return valid DecideResponse JSON.\n\n"
                        f"Runbooks:\n{catalog}"
                    ),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                }
            )
            response = self.bedrock_client.invoke_model(
                modelId=BEDROCK_MODEL_ID, body=body
            )
            text = json.loads(response["body"].read())["content"][0]["text"]
            start, end = text.find("{"), text.rfind("}")
            data = json.loads(text[start : end + 1])
            self.cost_guard.record_call()
            data["cost_cap_exceeded"] = False
            return data
        except Exception:
            result = self.rule_fallback.decide(anomaly_context)
            result["cost_cap_exceeded"] = self.cost_guard.is_cap_exceeded()
            return result


def _format_runbook_catalog(runbooks: dict) -> str:
    lines = []
    for name, rb in runbooks.items():
        lines.append(f"- {name}: {rb.get('description', '')}")
    return "\n".join(lines)


class DeciderService:
    def __init__(self):
        self._rule = RuleBasedDecider(RUNBOOKS_PATH)
        self._llm: Optional[LLMDecider] = None

    def decide(self, anomaly_context: dict) -> dict:
        if DECIDER_TYPE == "llm":
            if self._llm is None:
                self._llm = LLMDecider()
            return self._llm.decide(anomaly_context)
        return self._rule.decide(anomaly_context)
