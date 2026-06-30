import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import requests

from .config import (
    DATASET_DIR,
    DEFAULT_TELEMETRY_SOURCE_KIND,
    EVAL_BOCPD_WINDOW_AFTER,
    EVAL_BOCPD_WINDOW_BEFORE,
    K8S_CONTAINER_NAMES,
    K8S_CONTEXT,
    K8S_IN_CLUSTER,
    K8S_LABEL_SELECTOR,
    K8S_LOG_SINCE_SECONDS,
    K8S_LOG_TAIL_LINES,
    K8S_METRIC_WINDOW_SECONDS,
    K8S_METRICS_PROVIDER,
    K8S_NAMESPACE,
    K8S_SERVICE_LABEL_KEYS,
    LOKI_BASE_URL,
    PROMETHEUS_BASE_URL,
    PROMETHEUS_QUERY_STEP_SECONDS,
    PROMETHEUS_REQUEST_TIMEOUT_SECONDS,
)


class TelemetrySourceError(Exception):
    """Error raised by server-side telemetry providers."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def _iso(ts: int | float | datetime) -> str:
    if isinstance(ts, datetime):
        dt = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    else:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_list(value: Any, default: List[str]) -> List[str]:
    if value is None:
        return default
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return default


def _parse_rfc3339_prefix(line: str) -> tuple[str, str]:
    """Extract Kubernetes log timestamp when read with timestamps=True."""
    if not line:
        return _iso(datetime.now(timezone.utc)), ""
    first, _, rest = line.partition(" ")
    if not rest:
        return _iso(datetime.now(timezone.utc)), line
    try:
        # Python datetime supports microseconds, while K8s can return nanoseconds.
        normalized = first.replace("Z", "+00:00")
        if "." in normalized:
            head, tail = normalized.split(".", 1)
            match = re.match(r"(\d+)(.*)", tail)
            if match:
                frac = match.group(1)[:6].ljust(6, "0")
                tz = match.group(2)
                normalized = f"{head}.{frac}{tz}"
        dt = datetime.fromisoformat(normalized)
        return _iso(dt), rest
    except ValueError:
        return _iso(datetime.now(timezone.utc)), line


def _promql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _promql_regex(values: Iterable[str]) -> str:
    return "|".join(re.escape(v) for v in values if v)


def load_telemetry_from_source(source: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dispatch server-side telemetry loading by telemetry_source.kind."""
    source = dict(source or {})
    source_kind = source.get("kind") or DEFAULT_TELEMETRY_SOURCE_KIND
    if source_kind == "benchmark_fixture":
        return load_benchmark_fixture_telemetry(source)
    if source_kind == "k8s":
        return load_k8s_telemetry(source)
    if source_kind == "prometheus_loki":
        return load_prometheus_loki_telemetry(source)
    raise TelemetrySourceError(400, f"Unsupported telemetry_source kind: {source_kind}")


def load_benchmark_fixture_telemetry(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Deterministic benchmark provider: reads dataset/<service_fault>/<run_id> files.
    This remains the default for bench mode.
    """
    service_fault = source.get("service_fault")
    run_id = str(source.get("run_id"))
    if not service_fault or not run_id:
        raise TelemetrySourceError(400, "benchmark_fixture telemetry_source requires service_fault and run_id")

    run_dir = os.path.join(DATASET_DIR, service_fault, run_id)
    metrics_path = os.path.join(run_dir, "simple_metrics.csv")
    logs_path = os.path.join(run_dir, "logs.csv")
    if not os.path.exists(metrics_path):
        raise TelemetrySourceError(404, f"Benchmark telemetry not found: {metrics_path}")

    df_metrics = pd.read_csv(metrics_path).sort_values("time").reset_index(drop=True)
    original_rows = len(df_metrics)
    inject_time = source.get("inject_time")
    if inject_time is not None:
        window_before = int(source.get("window_before", EVAL_BOCPD_WINDOW_BEFORE))
        window_after = int(source.get("window_after", EVAL_BOCPD_WINDOW_AFTER))
        start_ts = int(inject_time) - window_before
        end_ts = int(inject_time) + window_after
        df_metrics = df_metrics[(df_metrics["time"] >= start_ts) & (df_metrics["time"] <= end_ts)].reset_index(drop=True)
        if df_metrics.empty:
            raise TelemetrySourceError(
                404,
                f"No telemetry rows in sliced window {start_ts}..{end_ts} for {service_fault}/{run_id}",
            )
        print(
            f"[API][TELEMETRY] Sliced benchmark telemetry around inject_time={inject_time}: "
            f"rows {original_rows} -> {len(df_metrics)} "
            f"(window_before={window_before}, window_after={window_after})"
        )

    tenant_id = str(source.get("tenant_id", "benchmark-cdo"))
    telemetry: List[Dict[str, Any]] = []
    for _, row in df_metrics.iterrows():
        ts = _iso(row["time"])
        for col, value in row.items():
            if col == "time" or pd.isna(value):
                continue
            telemetry.append({
                "ts": ts,
                "tenant_id": tenant_id,
                "service": str(col).split("_", 1)[0],
                "signal_name": str(col),
                "value": float(value),
                "labels": {},
            })

    if os.path.exists(logs_path):
        df_logs = pd.read_csv(logs_path)
        if inject_time is not None and "timestamp" in df_logs.columns:
            start_ns = (int(inject_time) - int(source.get("window_before", EVAL_BOCPD_WINDOW_BEFORE))) * 1_000_000_000
            end_ns = (int(inject_time) + int(source.get("window_after", EVAL_BOCPD_WINDOW_AFTER))) * 1_000_000_000
            df_logs = df_logs[(df_logs["timestamp"] >= start_ns) & (df_logs["timestamp"] <= end_ns)]
        for _, row in df_logs.iterrows():
            raw_ts = row.get("timestamp", df_metrics["time"].iloc[0] * 1_000_000_000)
            ts_sec = int(raw_ts // 1_000_000_000) if raw_ts > 10_000_000_000 else int(raw_ts)
            telemetry.append({
                "ts": _iso(ts_sec),
                "tenant_id": tenant_id,
                "service": str(row.get("container_name", "unknown")),
                "signal_name": "application_log_event",
                "value": str(row.get("message", "")),
                "labels": {"level": str(row.get("level", "info"))},
            })

    print(f"[API][TELEMETRY] Loaded benchmark source {service_fault}/{run_id}: {len(telemetry)} points")
    return telemetry


def _load_k8s_core_api(source: Dict[str, Any]):
    try:
        from kubernetes import client, config  # type: ignore
    except ImportError as exc:
        raise TelemetrySourceError(
            500,
            "Kubernetes telemetry requires dependency 'kubernetes'. Install requirements.txt in this environment.",
        ) from exc

    in_cluster = _parse_bool(source.get("in_cluster"), K8S_IN_CLUSTER)
    context = str(source.get("context", K8S_CONTEXT) or "").strip()
    try:
        if in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config(context=context or None)
    except Exception as exc:
        mode = "in-cluster" if in_cluster else f"kubeconfig context={context or '<default>'}"
        raise TelemetrySourceError(503, f"Failed to load Kubernetes config ({mode}): {exc}") from exc
    return client.CoreV1Api()


def _pod_service_name(pod: Any, service_label_keys: List[str]) -> str:
    labels = pod.metadata.labels or {}
    for key in service_label_keys:
        if labels.get(key):
            return str(labels[key])
    pod_name = str(pod.metadata.name)
    return re.sub(r"-[a-f0-9]{8,10}-[a-z0-9]{5}$", "", pod_name)


def _list_k8s_pods(v1: Any, source: Dict[str, Any]) -> list[Any]:
    namespace = str(source.get("namespace", K8S_NAMESPACE))
    label_selector = str(source.get("label_selector", K8S_LABEL_SELECTOR) or "").strip()
    try:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector).items
    except Exception as exc:
        raise TelemetrySourceError(503, f"Failed to list pods in namespace={namespace}: {exc}") from exc
    if not pods:
        selector = label_selector or "<none>"
        raise TelemetrySourceError(404, f"No pods found in namespace={namespace} label_selector={selector}")
    return pods


def _load_k8s_logs(source: Dict[str, Any], pods: list[Any], tenant_id: str, v1: Any) -> List[Dict[str, Any]]:
    namespace = str(source.get("namespace", K8S_NAMESPACE))
    since_seconds = int(source.get("log_since_seconds", K8S_LOG_SINCE_SECONDS))
    tail_lines = int(source.get("log_tail_lines", K8S_LOG_TAIL_LINES))
    service_label_keys = _as_list(source.get("service_label_keys"), K8S_SERVICE_LABEL_KEYS)
    configured_containers = _as_list(source.get("container_names"), K8S_CONTAINER_NAMES)
    telemetry: List[Dict[str, Any]] = []

    for pod in pods:
        pod_name = pod.metadata.name
        service = _pod_service_name(pod, service_label_keys)
        container_names = configured_containers or [c.name for c in (pod.spec.containers or [])]
        for container_name in container_names:
            try:
                log_text = v1.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=namespace,
                    container=container_name,
                    since_seconds=since_seconds,
                    tail_lines=tail_lines,
                    timestamps=True,
                )
            except Exception as exc:
                print(f"[API][TELEMETRY][WARN] Failed to read logs pod={pod_name} container={container_name}: {exc}")
                continue
            for raw_line in str(log_text or "").splitlines():
                ts, message = _parse_rfc3339_prefix(raw_line)
                if not message.strip():
                    continue
                telemetry.append({
                    "ts": ts,
                    "tenant_id": tenant_id,
                    "service": service,
                    "signal_name": "application_log_event",
                    "value": message,
                    "labels": {
                        "namespace": namespace,
                        "pod": pod_name,
                        "container": container_name,
                        "level": "info",
                    },
                })
    return telemetry


def _query_prometheus_range(query: str, start: datetime, end: datetime, step_seconds: int) -> List[Dict[str, Any]]:
    if not PROMETHEUS_BASE_URL:
        raise TelemetrySourceError(503, "PROMETHEUS_BASE_URL is required when K8S_METRICS_PROVIDER=prometheus")
    try:
        response = requests.get(
            f"{PROMETHEUS_BASE_URL}/api/v1/query_range",
            params={
                "query": query,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step_seconds,
            },
            timeout=PROMETHEUS_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise TelemetrySourceError(503, f"Prometheus query_range failed: {exc}") from exc
    if payload.get("status") != "success":
        raise TelemetrySourceError(503, f"Prometheus query failed: {payload}")
    return payload.get("data", {}).get("result", []) or []


def _load_prometheus_metrics(source: Dict[str, Any], pods: list[Any], tenant_id: str) -> List[Dict[str, Any]]:
    namespace = str(source.get("namespace", K8S_NAMESPACE))
    window_seconds = int(source.get("metric_window_seconds", K8S_METRIC_WINDOW_SECONDS))
    step_seconds = int(source.get("metric_step_seconds", PROMETHEUS_QUERY_STEP_SECONDS))
    service_label_keys = _as_list(source.get("service_label_keys"), K8S_SERVICE_LABEL_KEYS)
    end = datetime.now(timezone.utc)
    start = end - timedelta(seconds=window_seconds)
    pod_to_service = {pod.metadata.name: _pod_service_name(pod, service_label_keys) for pod in pods}
    pod_regex = _promql_regex(pod_to_service.keys())
    if not pod_regex:
        return []

    ns = _promql_escape(namespace)
    queries = {
        "cpu": (
            "sum by (pod) (rate(container_cpu_usage_seconds_total"
            f'{{namespace="{ns}",pod=~"{pod_regex}",container!="",image!=""}}[1m]))'
        ),
        "mem": (
            "sum by (pod) (container_memory_working_set_bytes"
            f'{{namespace="{ns}",pod=~"{pod_regex}",container!="",image!=""}})'
        ),
    }
    telemetry: List[Dict[str, Any]] = []
    for metric_type, query in queries.items():
        series_rows = _query_prometheus_range(query, start, end, step_seconds)
        for series in series_rows:
            pod_name = series.get("metric", {}).get("pod")
            service = pod_to_service.get(pod_name, pod_name or "unknown")
            for ts, raw_value in series.get("values", []):
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    continue
                telemetry.append({
                    "ts": _iso(float(ts)),
                    "tenant_id": tenant_id,
                    "service": service,
                    "signal_name": metric_type,
                    "value": value,
                    "labels": {"namespace": namespace, "pod": pod_name, "source": "prometheus"},
                })
    return telemetry


def load_k8s_telemetry(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Production K8s provider: reads pod logs via Kubernetes API and metrics via
    Prometheus when enabled. This function does not mutate Kubernetes resources.
    """
    tenant_id = str(source.get("tenant_id", "k8s-runtime"))
    metrics_provider = str(source.get("metrics_provider", K8S_METRICS_PROVIDER)).lower()
    v1 = _load_k8s_core_api(source)
    pods = _list_k8s_pods(v1, source)

    telemetry: List[Dict[str, Any]] = []
    if metrics_provider == "prometheus":
        telemetry.extend(_load_prometheus_metrics(source, pods, tenant_id))
    elif metrics_provider in {"none", "off", "disabled"}:
        print("[API][TELEMETRY] K8s metrics disabled; loading logs only")
    else:
        raise TelemetrySourceError(400, f"Unsupported K8S_METRICS_PROVIDER: {metrics_provider}")

    # Reuse the already loaded CoreV1Api config by reading logs after pod list.
    telemetry.extend(_load_k8s_logs(source, pods, tenant_id, v1))
    if not telemetry:
        raise TelemetrySourceError(404, "K8s telemetry source returned no logs or metrics")
    print(f"[API][TELEMETRY] Loaded k8s telemetry: pods={len(pods)} points={len(telemetry)}")
    return telemetry


def load_prometheus_loki_telemetry(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Compatibility alias for production mode. Metrics are loaded from Prometheus;
    logs currently reuse K8s pod logs unless Loki integration is added later.
    """
    if LOKI_BASE_URL:
        print("[API][TELEMETRY][WARN] LOKI_BASE_URL is configured but Loki log queries are not implemented yet; using K8s pod logs.")
    source = {**source, "kind": "k8s", "metrics_provider": source.get("metrics_provider", "prometheus")}
    return load_k8s_telemetry(source)