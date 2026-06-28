"""
Tenant Context extraction and cross-tenant validation.
Per AI API Contract §4: X-Tenant-Id header validation on all endpoints.
Per Deployment Contract §5: Multi-tenant isolation (≥2 tenants with RBAC).
"""
from dataclasses import dataclass
from typing import List, Optional
from fastapi import HTTPException, Header
import logging

logger = logging.getLogger(__name__)


@dataclass
class TenantContext:
    """Holds tenant identity extracted from X-Tenant-Id header."""
    tenant_id: str


def get_tenant_context(
    x_tenant_id: str = Header(..., description="Unique tenant identifier (UUID v4)")
) -> TenantContext:
    """FastAPI dependency that extracts X-Tenant-Id header into a TenantContext."""
    if not x_tenant_id or not x_tenant_id.strip():
        raise HTTPException(status_code=401, detail="Missing or empty X-Tenant-Id header.")
    return TenantContext(tenant_id=x_tenant_id.strip())


def validate_tenant_isolation(header_tenant_id: str, payload_tenant_ids: List[str]) -> None:
    """
    Cross-tenant validation: ensures every tenant_id in the payload matches the header.
    Per AI API Contract §4: returns 403 Forbidden on mismatch.
    """
    for tid in payload_tenant_ids:
        if tid != header_tenant_id:
            logger.warning(
                f"Tenant isolation violation: header={header_tenant_id}, payload={tid}"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Tenant isolation violation: header tenant_id '{header_tenant_id}' "
                       f"does not match payload tenant_id '{tid}'."
            )
