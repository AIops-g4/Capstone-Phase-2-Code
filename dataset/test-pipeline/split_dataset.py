import os
import glob
import random
import json
import csv
import shutil

# Import shared load_dotenv
from utils import load_dotenv

# Load environment configurations
load_dotenv()

# Set random seed for reproducibility
seed_val = int(os.environ.get("RANDOM_SEED", "42"))
random.seed(seed_val)

RAW_DIR = os.environ.get("RAW_DIR", "raw")
FILTERED_DIR = os.environ.get("FILTERED_DIR", "filtered")

def parse_case_info(case_path):
    """
    Extract system, faulty_service, and fault_type from case directory path.
    Works for both RE3-OB and RE2-OB.
    Example: raw/RE3-OB/adservice_f3 -> system='OB', service='adservice', fault='f3'
    Example: raw/RE2-OB/checkoutservice_cpu -> system='OB', service='checkoutservice', fault='cpu'
    """
    parts = case_path.split(os.sep)
    system_part = parts[-2]  # e.g., 'RE3-OB' or 'RE2-OB'
    case_name = parts[-1]    # e.g., 'adservice_f3' or 'checkoutservice_cpu'
    
    # Extract system name (OB)
    system = system_part.replace("RE3-", "").replace("RE2-", "")
    
    # Extract faulty service and fault type (split from the last occurrence of underscore)
    if "_" in case_name:
        r_idx = case_name.rfind("_")
        faulty_service = case_name[:r_idx]
        # Some RE3 cases could end in _f3_1
        # Check if the prefix before last underscore still contains a fault pattern
        if faulty_service.endswith("_f3") or faulty_service.endswith("_f4") or faulty_service.endswith("_f5"):
            # It's an f3_1 type case
            r_idx_2 = faulty_service.rfind("_")
            faulty_service = faulty_service[:r_idx_2]
            fault_type = case_name[r_idx_2+1:]
        else:
            fault_type = case_name[r_idx+1:]
    else:
        faulty_service = case_name
        fault_type = "unknown"
        
    return system, faulty_service, fault_type

def create_relative_symlink(src, dst):
    """
    Create a relative symbolic link from src to dst.
    """
    dst_dir = os.path.dirname(dst)
    os.makedirs(dst_dir, exist_ok=True)
    
    # Remove existing file/symlink if it exists
    if os.path.lexists(dst):
        if os.path.isdir(dst) and not os.path.islink(dst):
            shutil.rmtree(dst)
        else:
            os.remove(dst)
            
    # Compute relative path from dst_dir to src
    rel_src = os.path.relpath(src, start=dst_dir)
    os.symlink(rel_src, dst)

def get_candidate_services(traces_path):
    """
    Read the traces.csv file and extract all unique serviceName entries.
    """
    services = set()
    if not os.path.exists(traces_path):
        return []
    try:
        with open(traces_path, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader)
            try:
                svc_idx = header.index('serviceName')
            except ValueError:
                svc_idx = 3
            
            for row in reader:
                if len(row) > svc_idx:
                    svc = row[svc_idx].strip()
                    if svc:
                        services.add(svc)
    except Exception as e:
        print(f"  Warning: Error reading traces {traces_path}: {e}")
    return sorted(list(services))

def split_single_dataset(dataset_name):
    """
    Perform seed-reproducible stratified split for a single dataset (RE2-OB or RE3-OB).
    Returns a dict with 'train', 'val', 'public_test', 'private_test' lists.
    """
    print(f"\nProcessing splitting for: {dataset_name}...")
    case_dirs = sorted(glob.glob(os.path.join(RAW_DIR, dataset_name, "*")))
    
    all_runs = []
    for case_dir in case_dirs:
        system, faulty_service, fault_type = parse_case_info(case_dir)
        runs = sorted(glob.glob(os.path.join(case_dir, "*")))
        for run_dir in runs:
            run_id = os.path.basename(run_dir)
            if run_id.isdigit():
                traces_path = os.path.join(run_dir, "traces.csv")
                all_runs.append({
                    "src_path": run_dir,
                    "traces_path": traces_path,
                    "case_id": f"{dataset_name}/{os.path.basename(case_dir)}/{run_id}",
                    "dataset": dataset_name,
                    "system": system,
                    "faulty_service": faulty_service,
                    "fault_type": fault_type,
                    "case_name": os.path.basename(case_dir),
                    "run_id": run_id
                })
                
    print(f" - Found {len(all_runs)} runs in {dataset_name}.")
    
    # 1. Separate "unseen" service/cases
    # - RE3-OB: currencyservice_f1 (3 runs)
    # - RE2-OB: currencyservice_* (6 cases * 3 runs = 18 runs)
    unseen_cases = []
    seen_cases = []
    
    for run in all_runs:
        if run["faulty_service"] == "currencyservice":
            unseen_cases.append(run)
        else:
            seen_cases.append(run)
            
    print(f" - Selected {len(unseen_cases)} runs as 'unseen' faults for testing.")
    print(f" - Remaining {len(seen_cases)} runs to be split dynamically.")
    
    public_test_list = []
    private_test_list = []
    
    # Distribute unseen cases:
    # We group unseen by case_name so that runs of the same case are shuffled and distributed.
    unseen_by_case = {}
    for run in unseen_cases:
        unseen_by_case.setdefault(run["case_name"], []).append(run)
        
    for cname, runs in unseen_by_case.items():
        random.shuffle(runs)
        if len(runs) == 3:
            public_test_list.append(runs[0])
            private_test_list.extend(runs[1:])
        elif len(runs) == 4:
            public_test_list.extend(runs[:2])
            private_test_list.extend(runs[2:])
        else:
            split_idx = len(runs) // 3  # ~1/3 to public, ~2/3 to private
            if split_idx == 0:
                split_idx = 1
            public_test_list.extend(runs[:split_idx])
            private_test_list.extend(runs[split_idx:])
            
    # 2. Split seen cases:
    # Target proportions: Train 60%, Val 15%, Public Test 10%, Private Test 15%
    train_list = []
    val_list = []
    
    # Categorise hard/normal faults to prioritize hard cases for private_test
    if dataset_name == "RE3-OB":
        # RE3 hard: f3 (missing func), f5 (missing exception)
        hard_runs = [r for r in seen_cases if r["fault_type"] in ["f3", "f5"]]
        normal_runs = [r for r in seen_cases if r["fault_type"] not in ["f3", "f5"]]
        
        num_train = 16
        num_val = 4
        num_private = 4
    else:  # RE2-OB
        # RE2 hard: socket, loss, delay (network/complex socket issues)
        hard_runs = [r for r in seen_cases if r["fault_type"] in ["socket", "loss", "delay"]]
        normal_runs = [r for r in seen_cases if r["fault_type"] not in ["socket", "loss", "delay"]]
        
        num_train = 44
        num_val = 11
        num_private = 11
        
    random.shuffle(hard_runs)
    random.shuffle(normal_runs)
    
    # Allocate private_test from hard runs first
    sys_private = []
    while len(sys_private) < num_private and hard_runs:
        sys_private.append(hard_runs.pop())
    while len(sys_private) < num_private:
        sys_private.append(normal_runs.pop())
        
    # Allocate public_test from remaining runs
    num_runs = len(seen_cases)
    num_public = num_runs - num_train - num_val - num_private
    
    sys_public = []
    while len(sys_public) < num_public and hard_runs:
        sys_public.append(hard_runs.pop())
    while len(sys_public) < num_public:
        sys_public.append(normal_runs.pop())
        
    # Combine the rest for train and val
    remaining = hard_runs + normal_runs
    random.shuffle(remaining)
    
    sys_val = remaining[:num_val]
    sys_train = remaining[num_val:]
    
    train_list.extend(sys_train)
    val_list.extend(sys_val)
    public_test_list.extend(sys_public)
    private_test_list.extend(sys_private)
    
    return {
        "train": train_list,
        "val": val_list,
        "public_test": public_test_list,
        "private_test": private_test_list
    }

def main():
    print("Scanning raw dataset directories...")
    
    # Check if directories exist
    available_datasets = []
    for ds in ["RE2-OB", "RE3-OB"]:
        if os.path.exists(os.path.join(RAW_DIR, ds)):
            available_datasets.append(ds)
            
    if not available_datasets:
        print(f"Error: No RE2-OB or RE3-OB raw directories found in {RAW_DIR}.")
        sys.exit(1)
        
    print(f"Available datasets for splitting: {available_datasets}")
    
    # Run splitting for each dataset
    all_splits = {}
    for ds in available_datasets:
        all_splits[ds] = split_single_dataset(ds)
        
    # Merge splits
    merged_splits = {
        "train": [],
        "val": [],
        "public_test": [],
        "private_test": []
    }
    
    for split_name in merged_splits.keys():
        for ds in available_datasets:
            merged_splits[split_name].extend(all_splits[ds][split_name])
            
    # Clean up output filtered directory structure
    if os.path.exists(FILTERED_DIR):
        for split_name in merged_splits.keys():
            split_dir = os.path.join(FILTERED_DIR, split_name)
            if os.path.exists(split_dir):
                shutil.rmtree(split_dir)
                
    # Create symlinks and extract candidate services for Ground Truth
    print("\nCreating split directories and extracting candidate services (ground truth)...")
    for split_name, runs in merged_splits.items():
        print(f"Processing split: {split_name} ({len(runs)} cases)...")
        gt_data = {}
        
        for i, run in enumerate(runs):
            # 1. Create symlink
            # Target path: filtered/train/RE3-OB/adservice_f3/1 or filtered/train/RE2-OB/checkoutservice_cpu/1
            rel_case_dir = f"{run['dataset']}/{run['case_name']}/{run['run_id']}"
            dst_path = os.path.join(FILTERED_DIR, split_name, rel_case_dir)
            create_relative_symlink(run["src_path"], dst_path)
            
            # 2. Extract candidate services from traces.csv
            if i % 20 == 0 or i == len(runs) - 1:
                print(f"  Progress: {i+1}/{len(runs)}...")
                
            candidate_services = get_candidate_services(run["traces_path"])
            
            # Fallback check
            if run["faulty_service"] not in candidate_services:
                candidate_services.append(run["faulty_service"])
                candidate_services = sorted(candidate_services)
                
            gt_data[run["case_id"]] = {
                "root_cause_service": run["faulty_service"],
                "fault_type": run["fault_type"],
                "system": run["system"],
                "candidate_services": candidate_services
            }
            
        # Export Ground Truth to json
        gt_filename = os.path.join(FILTERED_DIR, f"{split_name}_gt.json")
        with open(gt_filename, 'w', encoding='utf-8') as f:
            json.dump(gt_data, f, indent=2)
        print(f"Saved ground truth mapping to {gt_filename}")
        
    print("\nDataset splitting completed successfully!")
    print(f"Combined split statistics:")
    for split_name, runs in merged_splits.items():
        re2_count = sum(1 for r in runs if r["dataset"] == "RE2-OB")
        re3_count = sum(1 for r in runs if r["dataset"] == "RE3-OB")
        print(f" - {split_name:12}: {len(runs):2} cases total | RE2-OB: {re2_count:2} | RE3-OB: {re3_count:2}")

if __name__ == "__main__":
    main()
