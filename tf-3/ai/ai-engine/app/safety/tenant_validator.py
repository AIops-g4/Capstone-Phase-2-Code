"""
Component 3 — Tenant Validator: Cross-tenant isolation validation.
Per AI API Contract §4: Validates X-Tenant-Id header matches payload tenant_id fields.
Returns 403 Forbidden on mismatch.
"""
from typing import List
from fastapi import HTTPException
from app.schemas.common import TelemetryPoint
import logging

logger = logging.getLogger(__name__)


class TenantValidator:
    """
    Validates tenant isolation across all endpoint types.
    Ensures payload tenant_id fields match the X-Tenant-Id header.
    """

    def validate_detect(self, header_tenant_id: str, telemetry_window: List[TelemetryPoint]) -> None:
        """Validate /v1/detect: every telemetry_window[].tenant_id must match header."""
        payload_ids = [point.tenant_id for point in telemetry_window]
        self._validate(header_tenant_id, payload_ids, "/v1/detect")

    def validate_verify(self, header_tenant_id: str, telemetry_window: List[TelemetryPoint]) -> None:
        """Validate /v1/verify: every post_telemetry_window[].tenant_id must match header."""
        payload_ids = [point.tenant_id for point in telemetry_window]
        self._validate(header_tenant_id, payload_ids, "/v1/verify")

    def _validate(self, header_tenant_id: str, payload_ids: List[str], endpoint: str) -> None:
        """Core validation: checks all payload tenant_ids against header."""
        try:
            from uuid import UUID
            header_uuid_str = str(UUID(header_tenant_id)).lower()
        except Exception:
            header_uuid_str = header_tenant_id.lower()

        for tid in payload_ids:
            tid_str = str(tid).lower()
            if tid_str != header_uuid_str:
                logger.warning(
                    f"Tenant isolation violation on {endpoint}: "
                    f"header={header_tenant_id}, payload={tid}"
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"Tenant isolation violation: header tenant_id "
                           f"'{header_tenant_id}' does not match payload "
                           f"tenant_id '{tid}'.",
                )
