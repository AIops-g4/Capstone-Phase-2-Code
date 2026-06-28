"""
Component 3 — Idempotency Lock using DynamoDB conditional write.
Per Deployment Contract §4: Prevents duplicate /v1/decide executions.
Returns 409 Conflict on duplicate idempotency_key + tenant_id.

NOTE: Uses in-memory fallback when DynamoDB is not available (local dev/testing).
"""
import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)


class IdempotencyLock:
    """
    DynamoDB-backed idempotency lock for /v1/decide endpoint.
    Lock key is scoped by tenant_id + idempotency_key.
    Uses conditional write (attribute_not_exists) for atomic check-and-set.
    """

    def __init__(self):
        self._in_memory_locks = set()  # Fallback for local dev
        try:
            self.dynamodb = boto3.resource('dynamodb', region_name=settings.BEDROCK_REGION)
            self.table = self.dynamodb.Table(settings.DYNAMODB_LOCK_TABLE)
            self._use_dynamodb = True
            logger.info(f"IdempotencyLock: Using DynamoDB table '{settings.DYNAMODB_LOCK_TABLE}'")
        except Exception as e:
            logger.warning(f"DynamoDB not available, using in-memory lock fallback: {e}")
            self._use_dynamodb = False

    def acquire_lock(self, tenant_id: str, idempotency_key: str) -> None:
        """
        Attempts to acquire an idempotency lock.
        Raises HTTPException(409) if the lock already exists (duplicate request).
        """
        lock_key = f"{tenant_id}::{idempotency_key}"

        if self._use_dynamodb:
            self._acquire_dynamodb(lock_key, tenant_id, idempotency_key)
        else:
            self._acquire_in_memory(lock_key)

    def _acquire_dynamodb(self, lock_key: str, tenant_id: str, idempotency_key: str) -> None:
        """DynamoDB conditional write — atomic check-and-set."""
        try:
            self.table.put_item(
                Item={
                    'lock_key': lock_key,
                    'tenant_id': tenant_id,
                    'idempotency_key': idempotency_key,
                },
                ConditionExpression='attribute_not_exists(lock_key)',
            )
            logger.info(f"Idempotency lock acquired: {lock_key}")
        except ClientError as e:
            if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
                logger.warning(f"Duplicate idempotency_key detected: {lock_key}")
                raise HTTPException(
                    status_code=409,
                    detail=f"Duplicate request: idempotency_key '{idempotency_key}' "
                           f"already processed for tenant '{tenant_id}'.",
                )
            else:
                logger.error(f"DynamoDB error acquiring lock: {e}")
                raise

    def _acquire_in_memory(self, lock_key: str) -> None:
        """In-memory fallback for local development."""
        if lock_key in self._in_memory_locks:
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate request: idempotency_key already processed.",
            )
        self._in_memory_locks.add(lock_key)
        logger.info(f"In-memory idempotency lock acquired: {lock_key}")
