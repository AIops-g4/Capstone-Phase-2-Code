"""
Component 3 — CostTracker: Budget controls for LLM usage.
Tracks daily accumulated costs for Bedrock API per tenant.
Updates are stored in DynamoDB table using conditional writes,
with a fallback to in-memory tracking if DynamoDB is unavailable.
"""
import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timezone
import decimal
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class CostTracker:
    """
    Tracks daily Bedrock usage cost per tenant.
    Utilizes a 'cost::[tenant_id]::[date]' lock_key in the idempotency-lock DynamoDB table.
    """

    def __init__(self):
        self._in_memory_cost = {}  # tenant_id -> {date: cost}
        try:
            self.dynamodb = boto3.resource('dynamodb', region_name=settings.BEDROCK_REGION)
            self.table = self.dynamodb.Table(settings.DYNAMODB_LOCK_TABLE)
            self._use_dynamodb = True
            logger.info(f"CostTracker: Initialized with DynamoDB table '{settings.DYNAMODB_LOCK_TABLE}'")
        except Exception as e:
            logger.warning(f"CostTracker: DynamoDB not available, using in-memory tracking: {e}")
            self._use_dynamodb = False

    def _get_key(self, tenant_id: str) -> str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return f"cost::{tenant_id}::{date_str}"

    def get_todays_cost(self, tenant_id: str) -> float:
        """Retrieves today's accumulated cost for a tenant."""
        key = self._get_key(tenant_id)
        if self._use_dynamodb:
            try:
                resp = self.table.get_item(Key={'lock_key': key})
                item = resp.get('Item')
                if item:
                    return float(item.get('daily_cost', 0.0))
            except Exception as e:
                logger.error(f"Error getting today's cost from DynamoDB: {e}")
            return 0.0
        else:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            return self._in_memory_cost.get(tenant_id, {}).get(date_str, 0.0)

    def increment_cost(self, tenant_id: str, cost: float) -> float:
        """Atomically increments today's cost counter for a tenant."""
        key = self._get_key(tenant_id)
        if self._use_dynamodb:
            try:
                # Update table using atomic ADD operation
                resp = self.table.update_item(
                    Key={'lock_key': key},
                    UpdateExpression="ADD daily_cost :c SET tenant_id = :t, ttl = :ttl",
                    ExpressionAttributeValues={
                        ':c': decimal.Decimal(str(cost)),
                        ':t': tenant_id,
                        ':ttl': int(datetime.now(timezone.utc).timestamp() + 172800)  # Expire in 2 days (172800s)
                    },
                    ReturnValues="UPDATED_NEW"
                )
                updated = resp.get('Attributes', {})
                new_cost = float(updated.get('daily_cost', 0.0))
                logger.info(f"Tenant {tenant_id} daily Bedrock cost updated: ${new_cost:.5f}")
                return new_cost
            except Exception as e:
                logger.error(f"Error incrementing daily cost in DynamoDB: {e}")
                # Fallback to local in-memory update on error to avoid halting processing
                return self._increment_in_memory(tenant_id, cost)
        else:
            return self._increment_in_memory(tenant_id, cost)

    def _increment_in_memory(self, tenant_id: str, cost: float) -> float:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tenant_costs = self._in_memory_cost.setdefault(tenant_id, {})
        new_cost = tenant_costs.get(date_str, 0.0) + cost
        tenant_costs[date_str] = new_cost
        logger.info(f"Tenant {tenant_id} daily Bedrock cost updated (in-memory): ${new_cost:.5f}")
        return new_cost
