import os
import json
import requests
import datetime
import threading
from typing import Optional, Dict, Any

class CostTracker:
    _lock = threading.Lock()
    # (tenant_id, date_str) -> cumulative_cost
    _daily_costs = {}

    @classmethod
    def get_cost(cls, tenant_id: str) -> float:
        date_str = datetime.date.today().isoformat()
        with cls._lock:
            return cls._daily_costs.get((tenant_id, date_str), 0.0)

    @classmethod
    def add_cost(cls, tenant_id: str, cost: float):
        date_str = datetime.date.today().isoformat()
        key = (tenant_id, date_str)
        with cls._lock:
            current = cls._daily_costs.get(key, 0.0)
            cls._daily_costs[key] = current + cost
            print(f"[CostTracker] Added ${cost:.5f} to tenant={tenant_id}. New daily total: ${cls._daily_costs[key]:.5f}")


class BaseLLMClient:
    """
    Abstract interface for all LLM providers (OpenAI, Anthropic, Bedrock).
    """
    def generate_decision(self, prompt: str, tenant_id: str = "default-tenant") -> str:
        raise NotImplementedError("Subclasses must implement generate_decision.")


class OpenAILLMClient(BaseLLMClient):
    """
    Client for OpenAI API, communicating over HTTP.
    """
    def __init__(self, api_key: str, model: str = "gpt-4o", api_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        url = api_url or os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")
        if not url.endswith("/chat/completions"):
            url = url.rstrip("/") + "/chat/completions"
        self.api_url = url

    def generate_decision(self, prompt: str, tenant_id: str = "default-tenant") -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0,
            "response_format": {"type": "json_object"}
        }
        try:
            res = requests.post(self.api_url, json=payload, headers=headers, timeout=30.0)
            res.raise_for_status()
            res_data = res.json()
            
            # Compute token usage cost
            usage = res_data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            # OpenAI pricing estimate: $2.50 / 1M input, $10.00 / 1M output
            cost = (input_tokens * 2.5 / 1_000_000) + (output_tokens * 10.0 / 1_000_000)
            CostTracker.add_cost(tenant_id, cost)
            
            return res_data["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"OpenAI API call failed: {e}")


class AnthropicLLMClient(BaseLLMClient):
    """
    Client for Anthropic API, communicating over HTTP.
    """
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022", api_url: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        url = api_url or os.getenv("ANTHROPIC_API_URL", "https://api.anthropic.com/v1/messages")
        if not url.endswith("/messages"):
            url = url.rstrip("/") + "/messages"
        self.api_url = url

    def generate_decision(self, prompt: str, tenant_id: str = "default-tenant") -> str:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        payload = {
            "model": self.model,
            "max_tokens": 2048,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0
        }
        try:
            res = requests.post(self.api_url, json=payload, headers=headers, timeout=30.0)
            res.raise_for_status()
            res_data = res.json()
            
            # Compute token usage cost
            usage = res_data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            # Claude 3.5 Sonnet pricing: $3.00 / 1M input, $15.00 / 1M output
            cost = (input_tokens * 3.0 / 1_000_000) + (output_tokens * 15.0 / 1_000_000)
            CostTracker.add_cost(tenant_id, cost)
            
            return res_data["content"][0]["text"]
        except Exception as e:
            raise RuntimeError(f"Anthropic API call failed: {e}")


class BedrockLLMClient(BaseLLMClient):
    """
    Client for AWS Bedrock Runtime, communicating via boto3 converse API.
    """
    def __init__(
        self, 
        aws_access_key: Optional[str] = None, 
        aws_secret_key: Optional[str] = None, 
        aws_session_token: Optional[str] = None,
        region: str = "us-east-1",
        model: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        endpoint_url: Optional[str] = None
    ):
        self.aws_access_key = aws_access_key or os.getenv("AWS_ACCESS_KEY_ID")
        self.aws_secret_key = aws_secret_key or os.getenv("AWS_SECRET_ACCESS_KEY")
        self.aws_session_token = aws_session_token or os.getenv("AWS_SESSION_TOKEN")
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.model = model
        self.endpoint_url = endpoint_url or os.getenv("AWS_ENDPOINT_URL")

    def generate_decision(self, prompt: str, tenant_id: str = "default-tenant") -> str:
        try:
            import boto3
            client = boto3.client(
                service_name="bedrock-runtime",
                region_name=self.region,
                aws_access_key_id=self.aws_access_key,
                aws_secret_access_key=self.aws_secret_key,
                aws_session_token=self.aws_session_token,
                endpoint_url=self.endpoint_url
            )
            
            response = client.converse(
                modelId=self.model,
                messages=[
                    {"role": "user", "content": [{"text": prompt}]}
                ],
                inferenceConfig={
                    "temperature": 0.0,
                    "maxTokens": 2048
                }
            )
            
            # Compute token usage cost
            usage = response.get("usage", {})
            input_tokens = usage.get("inputTokens", 0)
            output_tokens = usage.get("outputTokens", 0)
            # Claude 3.5 Sonnet pricing: $3.00 / 1M input, $15.00 / 1M output
            cost = (input_tokens * 3.0 / 1_000_000) + (output_tokens * 15.0 / 1_000_000)
            CostTracker.add_cost(tenant_id, cost)
            
            return response['output']['message']['content'][0]['text']
        except Exception as e:
            raise RuntimeError(f"AWS Bedrock converse call failed: {e}")


class LLMFactory:
    """
    Factory to instantiate the appropriate LLM client based on configuration.
    """
    @staticmethod
    def get_client() -> BaseLLMClient:
        provider = os.getenv("LLM_PROVIDER", "openai").lower()
        
        if provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            model = os.getenv("LLM_MODEL", "gpt-4o")
            api_url = os.getenv("OPENAI_API_URL")
            if not api_key:
                raise ValueError("OPENAI_API_KEY environment variable is not set.")
            return OpenAILLMClient(api_key=api_key, model=model, api_url=api_url)
            
        elif provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            model = os.getenv("LLM_MODEL", "claude-3-5-sonnet-20241022")
            api_url = os.getenv("ANTHROPIC_API_URL")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")
            return AnthropicLLMClient(api_key=api_key, model=model, api_url=api_url)
            
        elif provider == "bedrock":
            access_key = os.getenv("AWS_ACCESS_KEY_ID")
            secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
            session_token = os.getenv("AWS_SESSION_TOKEN")
            region = os.getenv("AWS_REGION", "us-east-1")
            model = os.getenv("LLM_MODEL", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")
            endpoint_url = os.getenv("AWS_ENDPOINT_URL")
            return BedrockLLMClient(
                aws_access_key=access_key,
                aws_secret_key=secret_key,
                aws_session_token=session_token,
                region=region,
                model=model,
                endpoint_url=endpoint_url
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")
