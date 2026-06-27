from pydantic import BaseModel, Field
from typing import Optional, Dict, Union

class AnomalyContext(BaseModel):
    target_service: str
    suspected_fault_type: str
    system: str
    namespace: Optional[str] = None
    deployment: Optional[str] = None
    trigger_metric: Optional[str] = None
    trigger_value: Optional[float] = None

class TelemetryPoint(BaseModel):
    ts: str
    tenant_id: str
    service: str
    signal_name: str
    value: Union[float, str]
    labels: Optional[Dict[str, str]] = None
