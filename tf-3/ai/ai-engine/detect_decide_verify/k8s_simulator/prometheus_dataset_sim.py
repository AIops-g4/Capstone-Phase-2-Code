#!/usr/bin/env python3
"""Minimal Prometheus query_range simulator backed by benchmark simple_metrics.csv.

It implements /api/v1/query_range enough for src.telemetry_sources._load_prometheus_metrics:
- CPU query returns <service>_cpu values.
- Memory query returns <service>_mem values.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd


class DatasetPrometheus:
    def __init__(self, dataset_dir: str, service_fault: str, run_id: str):
        run_dir = os.path.join(dataset_dir, service_fault, str(run_id))
        metrics_path = os.path.join(run_dir, "simple_metrics.csv")
        if not os.path.exists(metrics_path):
            raise FileNotFoundError(metrics_path)
        self.df = pd.read_csv(metrics_path).sort_values("time").reset_index(drop=True)
        self.columns = set(self.df.columns)
        print(f"[prom-sim] loaded {metrics_path} rows={len(self.df)}")

    def query_range(self, query: str, start: float | None, end: float | None, step: int) -> dict:
        metric_type = "cpu" if "container_cpu_usage_seconds_total" in query else "mem"
        pod_regex = ".*"
        match = re.search(r'pod=~"([^"]+)"', query)
        if match:
            pod_regex = match.group(1)
        pods = [p for p in pod_regex.split("|") if p]
        if not pods:
            pods = ["checkoutservice", "currencyservice", "emailservice", "productcatalogservice", "recommendationservice"]

        df = self.df
        if start is not None and end is not None:
            sliced = df[(df["time"] >= start) & (df["time"] <= end)]
            # Real Prometheus would return current window only. For replay, if wall-clock
            # start/end do not overlap historical dataset timestamps, return full replay.
            if not sliced.empty:
                df = sliced

        results = []
        for pod in pods:
            normalized_pod = pod.replace("\\-", "-")
            service = re.sub(r"-[a-z0-9]+-[a-z0-9]+$", "", normalized_pod)
            col = f"{service}_{metric_type}"
            if col not in self.columns:
                continue
            values = [[float(row["time"]), str(float(row[col]))] for _, row in df.iterrows() if pd.notna(row[col])]
            # Return the real pod name, not the escaped regex token, so
            # telemetry_sources.py can map pod -> logical service via pod labels.
            results.append({"metric": {"pod": normalized_pod}, "values": values})
        return {"status": "success", "data": {"resultType": "matrix", "result": results}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-dir", default=os.path.join(os.path.dirname(__file__), "..", "..", "dataset"))
    parser.add_argument("--service-fault", default="checkoutservice_cpu")
    parser.add_argument("--run-id", default="1")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19090)
    args = parser.parse_args()
    sim = DatasetPrometheus(args.dataset_dir, args.service_fault, args.run_id)
    print("[prom-sim] available metric columns=" + ",".join(sorted(sim.columns)), flush=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args):
            print("[prom-sim] " + fmt % args)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/-/ready":
                body = b"ready\n"
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path != "/api/v1/query_range":
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
                return
            qs = parse_qs(parsed.query)
            query = qs.get("query", [""])[0]
            try:
                start = float(qs.get("start", ["nan"])[0])
                end = float(qs.get("end", ["nan"])[0])
            except ValueError:
                start = end = None
            step_raw = qs.get("step", ["15"])[0]
            try:
                step = int(float(step_raw))
            except ValueError:
                step = 15
            payload = sim.query_range(query, start, end, step)
            result_count = len(payload.get("data", {}).get("result", []))
            print(f"[prom-sim] query_range result_count={result_count} query={query[:160]}", flush=True)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[prom-sim] listening on http://{args.host}:{args.port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
