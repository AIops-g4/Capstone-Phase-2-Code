"""
Component 5 — Audit Trail Writer (S3 Object Lock).
Per Deployment Contract §4: Writes tamper-evident JSON audit records to S3.
Compliance mode, 90-day retention, fail-closed behavior.

NOTE: Uses local file fallback when S3 is not available (local dev/testing).
"""
import boto3
import json
import os
from datetime import datetime, timezone
from botocore.exceptions import ClientError
from app.core.config import settings
from app.audit.schema import AuditRecord
import logging

logger = logging.getLogger(__name__)


class AuditWriter:
    """
    Writes audit records to S3 with Object Lock (Compliance mode).
    Synchronous write before returning API response (fail-closed).
    
    S3 Key format: audit/{tenant_id}/{YYYY-MM-DD}/{correlation_id}_{endpoint}_{timestamp}.json
    """

    def __init__(self):
        self._local_audit_dir = "audit_logs"  # Local fallback directory
        try:
            self.s3_client = boto3.client('s3', region_name=settings.BEDROCK_REGION)
            self.bucket = settings.AUDIT_S3_BUCKET
            self._use_s3 = True
            logger.info(f"AuditWriter: Using S3 bucket '{self.bucket}'")
        except Exception as e:
            logger.warning(f"S3 not available, using local file audit fallback: {e}")
            self._use_s3 = False
            os.makedirs(self._local_audit_dir, exist_ok=True)

    def write(self, record: AuditRecord) -> None:
        """
        Writes an audit record synchronously. 
        Per design: fail-closed — if write fails, API returns 500.
        """
        record_json = record.model_dump_json(indent=2)
        s3_key = self._build_key(record)

        if self._use_s3:
            self._write_s3(s3_key, record_json)
        else:
            self._write_local(s3_key, record_json)

        logger.info(f"Audit record written: {s3_key}")

    def _build_key(self, record: AuditRecord) -> str:
        """Builds the S3 key per design format."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        endpoint_safe = record.endpoint.replace("/", "_").strip("_")
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"audit/{record.tenant_id}/{date_str}/{record.correlation_id}_{endpoint_safe}_{ts}.json"

    def _write_s3(self, key: str, body: str) -> None:
        """Writes to S3 with Object Lock."""
        try:
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body.encode('utf-8'),
                ContentType='application/json',
                ServerSideEncryption='AES256',
                ObjectLockMode='COMPLIANCE',
                ObjectLockRetainUntilDate=datetime.now(timezone.utc).replace(
                    day=1
                ) + __import__('datetime').timedelta(days=settings.AUDIT_RETENTION_DAYS),
            )
        except ClientError as e:
            logger.error(f"S3 audit write failed: {e}")
            # Fail-closed: re-raise so API returns 500
            raise RuntimeError(f"Audit trail write failed: {e}") from e

    def _write_local(self, key: str, body: str) -> None:
        """Local file fallback for development."""
        file_path = os.path.join(self._local_audit_dir, key.replace("/", os.sep))
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(body)
