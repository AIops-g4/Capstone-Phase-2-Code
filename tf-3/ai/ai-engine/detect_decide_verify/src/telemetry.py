import pandas as pd
import numpy as np
from typing import List, Tuple, Dict, Any
from .log_parser import Drain3LogParser
from .config import SERVICES_LIST

class TelemetryProcessor:
    """
    Encapsulates ingestion, reconstruction, cleaning, and parsing of raw telemetry streams.
    """
    def __init__(self, log_parser: Drain3LogParser = None):
        self.log_parser = log_parser or Drain3LogParser(service_aware=True)

    def process_telemetry_window(
        self, 
        telemetry_window: List[Any]
    ) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, Any]]:
        """
        Reconstructs aligned metrics and logs DataFrames from a raw list of TelemetryPoints.
        
        Returns:
        - df_metrics: Aligned, ffilled, sorted metrics DataFrame.
        - df_log_ts: Aggregated log template frequency timeseries.
        - temp_info: Metadata dictionary mapping log templates to containers/levels.
        """
        metrics_records = {}
        log_messages = []

        def _get(point: Any, key: str, default: Any = None) -> Any:
            # Support both dict telemetry points (from telemetry_source loaders)
            # and Pydantic TelemetryPoint objects (from direct CDO push).
            if isinstance(point, dict):
                return point.get(key, default)
            return getattr(point, key, default)

        # 1. Parse raw telemetry points
        for point in telemetry_window:
            ts = _get(point, "ts")
            signal_name = _get(point, "signal_name")
            service = _get(point, "service")
            value = _get(point, "value")
            labels = _get(point, "labels")
            ts_sec = int(pd.to_datetime(ts).timestamp())

            if signal_name == "application_log_event":
                log_messages.append({
                    "timestamp": ts_sec * 1000000000,
                    "container_name": service,
                    "message": str(value),
                    "level": labels.get("level", "info") if labels else "info"
                })
            else:
                if ts_sec not in metrics_records:
                    metrics_records[ts_sec] = {"time": ts_sec}

                # Format column name matching simple_metrics format (e.g. adservice_cpu)
                col_name = signal_name
                if not any(str(signal_name).startswith(s) for s in SERVICES_LIST if s != "redis" and s != "frontend"):
                    col_name = f"{service}_{signal_name}"

                metrics_records[ts_sec][col_name] = float(value)
                
        if not metrics_records:
            return pd.DataFrame(), pd.DataFrame(), {}
            
        # 2. Reconstruct and clean metrics DataFrame
        df_metrics = pd.DataFrame(list(metrics_records.values())).sort_values("time").reset_index(drop=True)
        df_metrics = df_metrics.ffill().fillna(0)
        
        # 3. Reconstruct and parse logs DataFrame
        df_logs = pd.DataFrame(log_messages)
        time_start = int(df_metrics["time"].min())
        time_end = int(df_metrics["time"].max())
        
        df_log_ts, temp_info = self.log_parser.parse_logs(df_logs, time_start, time_end)
        
        return df_metrics, df_log_ts, temp_info
