import os
import glob
import random
import json
import csv
import shutil

# Set random seed for reproducibility
random.seed(42)

RAW_DIR = "raw"
FILTERED_DIR = "filtered"

def get_candidate_services(traces_path):
    """
    Read the traces.csv file and extract all unique serviceName entries.
    Uses pure Python standard csv module for speed and compatibility.
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
                # Fallback to column index 3 (standard for the traces.csv structure)
                svc_idx = 3
            
            for row in reader:
                if len(row) > svc_idx:
                    svc = row[svc_idx].strip()
                    if svc:
                        services.add(svc)
    except Exception as e:
        print(f"  Warning: Error reading traces {traces_path}: {e}")
    return sorted(list(services))

def parse_case_info(case_path):
    """
    Extract system, faulty_service, and fault_type from case directory path.
    Example: raw/RE3-OB/adservice_f3 -> system='OB', service='adservice', fault='f3'
    """
    parts = case_path.split(os.sep)
    system_part = parts[-2]  # e.g., 'RE3-OB'
    case_name = parts[-1]    # e.g., 'adservice_f3'
    
    # Extract system name (OB, SS, TT)
    system = system_part.replace("RE3-", "")
    
    # Extract faulty service and fault type
    if "_" in case_name:
        # Some cases might have suffixes, e.g., ts-route-service_f3_1
        # Split from the last occurrence of _f[1-5]
        # Or simply partition at the first _f
        idx = case_name.find("_f")
        if idx != -1:
            faulty_service = case_name[:idx]
            fault_type = case_name[idx+1:]
        else:
            # Fallback split
            r_idx = case_name.rfind("_")
            faulty_service = case_name[:r_idx]
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

def main():
    print("Scanning raw dataset directories...")
    # Find all case directories
    case_dirs = sorted(glob.glob(os.path.join(RAW_DIR, "RE3-*", "*")))
    
    all_runs = []
    
    for case_dir in case_dirs:
        system, faulty_service, fault_type = parse_case_info(case_dir)
        # Find all run subdirectories (should be numeric)
        runs = sorted(glob.glob(os.path.join(case_dir, "*")))
        for run_dir in runs:
            run_id = os.path.basename(run_dir)
            if run_id.isdigit():
                traces_path = os.path.join(run_dir, "traces.csv")
                all_runs.append({
                    "src_path": run_dir,
                    "traces_path": traces_path,
                    "case_id": f"RE3-{system}/{os.path.basename(case_dir)}/{run_id}",
                    "system": system,
                    "faulty_service": faulty_service,
                    "fault_type": fault_type,
                    "case_name": os.path.basename(case_dir),
                    "run_id": run_id
                })
                
    print(f"Found total {len(all_runs)} runs (failure cases) across all systems.")
    
    # 1. Separate "unseen" service/cases to make tests difficult
    # - OB: currencyservice_f1 (3 runs) -> completely unseen in train/val
    # - SS: carts_f4 (4 runs) -> completely unseen in train/val
    # - TT: ts-route-service_f3_1 (3 runs) -> completely unseen in train/val
    unseen_cases = []
    seen_cases = []
    
    for run in all_runs:
        is_unseen = False
        if run["system"] == "OB" and run["faulty_service"] == "currencyservice":
            is_unseen = True
        elif run["system"] == "SS" and run["case_name"] == "carts_f4":
            # Note: Sock Shop has carts_f1, carts_f3, carts_f4. Only carts_f4 is unseen.
            is_unseen = True
        elif run["system"] == "TT" and run["case_name"] == "ts-route-service_f3_1":
            is_unseen = True
            
        if is_unseen:
            unseen_cases.append(run)
        else:
            seen_cases.append(run)
            
    print(f"Selected {len(unseen_cases)} runs as 'unseen' faults for testing.")
    print(f"Remaining {len(seen_cases)} runs to be split dynamically.")
    
    # Split the unseen cases:
    # We want public_test and private_test to contain them.
    # Distribute:
    # OB currencyservice_f1 (3 runs): 1 to public_test, 2 to private_test
    # SS carts_f4 (4 runs): 2 to public_test, 2 to private_test
    # TT ts-route-service_f3_1 (3 runs): 1 to public_test, 2 to private_test
    
    public_test_list = []
    private_test_list = []
    
    # Group unseen by case_name
    unseen_by_case = {}
    for run in unseen_cases:
        unseen_by_case.setdefault(run["case_name"], []).append(run)
        
    for cname, runs in unseen_by_case.items():
        # Shuffle runs locally
        random.shuffle(runs)
        if len(runs) == 3:
            public_test_list.append(runs[0])
            private_test_list.extend(runs[1:])
        elif len(runs) == 4:
            public_test_list.extend(runs[:2])
            private_test_list.extend(runs[2:])
        else:
            # Fallback
            split_idx = len(runs) // 2
            public_test_list.extend(runs[:split_idx])
            private_test_list.extend(runs[split_idx:])

    # 2. Split seen cases:
    # We will stratify by system to keep balance, and ensure F3 and F5 are well-represented in private_test.
    train_list = []
    val_list = []
    
    # Group seen cases by system
    seen_by_sys = {}
    for run in seen_cases:
        seen_by_sys.setdefault(run["system"], []).append(run)
        
    for sys, runs in seen_by_sys.items():
        # Target splits for remaining runs in this system:
        # OB: 27 runs -> 16 train, 4 val, 3 public_test, 4 private_test
        # SS: 26 runs -> 16 train, 4 val, 2 public_test, 4 private_test
        # TT: 27 runs -> 16 train, 4 val, 3 public_test, 4 private_test
        
        # Sort runs by fault_type to implement a primitive stratified selection
        # Specifically, we want to prioritize F3 and F5 for private_test
        # We can rank runs so that F3 and F5 are grouped, then shuffle and select.
        # But to be precise, let's categorize runs into: hard (f3, f5) and normal (f1, f2, f4)
        hard_runs = [r for r in runs if r["fault_type"] in ["f3", "f5"]]
        normal_runs = [r for r in runs if r["fault_type"] not in ["f3", "f5"]]
        
        random.shuffle(hard_runs)
        random.shuffle(normal_runs)
        
        # Determine number of targets for this system
        num_runs = len(runs)
        num_train = 16
        num_val = 4
        
        # Total test budget for this system:
        # OB: 11 test => 3 public, 4 private (seen) + 3 unseen
        # SS: 10 test => 2 public, 4 private (seen) + 4 unseen
        # TT: 11 test => 3 public, 4 private (seen) + 3 unseen
        num_private = 4
        num_public = num_runs - num_train - num_val - num_private
        
        # Allocate private_test first from hard runs to make it extra difficult
        sys_private = []
        while len(sys_private) < num_private and hard_runs:
            sys_private.append(hard_runs.pop())
        # If not enough hard runs, take from normal
        while len(sys_private) < num_private:
            sys_private.append(normal_runs.pop())
            
        # Allocate public_test from remaining runs
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
        
    splits = {
        "train": train_list,
        "val": val_list,
        "public_test": public_test_list,
        "private_test": private_test_list
    }
    
    # 3. Create symlinks and extract candidate services for GT
    print("\nCreating split directories and extracting candidate services (ground truth)...")
    
    # Clean up output filtered directory structure
    if os.path.exists(FILTERED_DIR):
        # We delete only splits folders to prevent deleting other contents
        for split_name in splits.keys():
            split_dir = os.path.join(FILTERED_DIR, split_name)
            if os.path.exists(split_dir):
                shutil.rmtree(split_dir)
                
    for split_name, runs in splits.items():
        print(f"Processing split: {split_name} ({len(runs)} cases)...")
        gt_data = {}
        
        for i, run in enumerate(runs):
            # 1. Create symlink
            # Target path: filtered/train/RE3-OB/adservice_f3/1
            rel_case_dir = f"RE3-{run['system']}/{run['case_name']}/{run['run_id']}"
            dst_path = os.path.join(FILTERED_DIR, split_name, rel_case_dir)
            create_relative_symlink(run["src_path"], dst_path)
            
            # 2. Extract candidate services from traces.csv (only if not already cached in memory)
            if i % 10 == 0 or i == len(runs) - 1:
                print(f"  Progress: {i+1}/{len(runs)}...")
                
            candidate_services = get_candidate_services(run["traces_path"])
            
            # Make sure root cause is in candidate services just in case traces.csv didn't capture it (fallback)
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
    print(f"Split statistics:")
    for split_name, runs in splits.items():
        # Count TT cases in each split to verify difficulty
        tt_count = sum(1 for r in runs if r["system"] == "TT")
        hard_count = sum(1 for r in runs if r["fault_type"] in ["f3", "f5"])
        print(f" - {split_name:12}: {len(runs):2} cases | TT cases: {tt_count:2} | Hard faults (F3/F5): {hard_count:2}")

if __name__ == "__main__":
    main()
