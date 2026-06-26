"""
Download RE3 dataset (per detect/README.md), build ground_truth.json, and seed runbooks.

Data lands in ai-engine/dataset/ (shared by detect and decide).
"""
import json
import os
import re
import subprocess
import sys
import zipfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DECIDE_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, DECIDE_DIR)

from src.config import DATASET_DIR, GROUND_TRUTH_PATH, RUNBOOKS_PATH
from src.runbook_catalog import resolve_runbook_for_fault, write_runbooks

RE3_GDRIVE_ID = "1cZpnaZ1ijLUBssXzCnbGVWsT1NlnXtoy"
FAULT_FOLDER_RE = re.compile(r"^(.+)_(f[1-5]|[a-z]+)$")


def _ensure_gdown():
    try:
        import gdown  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "gdown"])


def parse_service_fault(folder: str) -> tuple[str, str] | tuple[None, None]:
    m = FAULT_FOLDER_RE.match(folder)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def download_re3() -> str:
    os.makedirs(DATASET_DIR, exist_ok=True)
    zip_path = os.path.join(DATASET_DIR, "re3.zip")
    if not os.path.exists(zip_path):
        _ensure_gdown()
        import gdown

        print(f"[DOWNLOAD] Fetching RE3 from Google Drive -> {zip_path}")
        gdown.download(id=RE3_GDRIVE_ID, output=zip_path, quiet=False)
    else:
        print(f"[SKIP] Already downloaded: {zip_path}")

    print("[EXTRACT] Unzipping RE3...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(DATASET_DIR)
    return DATASET_DIR


def _scan_roots(dataset_dir: str) -> list:
    roots = [dataset_dir]
    for name in os.listdir(dataset_dir):
        path = os.path.join(dataset_dir, name)
        if os.path.isdir(path) and name.upper().startswith("RE3"):
            roots.append(path)
    return roots


def build_ground_truth(dataset_dir: str) -> dict:
    ground_truth = {}
    for root in _scan_roots(dataset_dir):
        for folder in sorted(os.listdir(root)):
            folder_path = os.path.join(root, folder)
            if not os.path.isdir(folder_path):
                continue
            target_service, suspected_fault_type = parse_service_fault(folder)
            if not target_service:
                continue

            for run_dir in sorted(os.listdir(folder_path)):
                run_path = os.path.join(folder_path, run_dir)
                if not os.path.isdir(run_path) or not run_dir.isdigit():
                    continue
                inject_file = os.path.join(run_path, "inject_time.txt")
                if not os.path.exists(inject_file):
                    continue
                with open(inject_file, "r", encoding="utf-8") as f:
                    try:
                        inject_time = int(f.read().strip())
                    except ValueError:
                        inject_time = 0

                run_key = f"{folder}_{run_dir}"
                ground_truth[run_key] = {
                    "service_fault": folder,
                    "run_id": run_dir,
                    "target_service": target_service,
                    "suspected_fault_type": suspected_fault_type,
                    "inject_time": inject_time,
                    "dataset": "re3",
                    "matched_runbook": resolve_runbook_for_fault(suspected_fault_type),
                }
    return ground_truth


def main():
    print("=" * 60)
    print("  DECIDE — RE3 dataset setup")
    print("=" * 60)
    download_re3()
    write_runbooks()
    print(f"[OK] Runbooks written to {RUNBOOKS_PATH}")

    gt = build_ground_truth(DATASET_DIR)
    with open(GROUND_TRUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(gt, f, indent=2)
    print(f"[OK] ground_truth.json: {len(gt)} runs -> {GROUND_TRUTH_PATH}")

    if not gt:
        print("[WARN] No runs found. Check zip layout under dataset/.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
