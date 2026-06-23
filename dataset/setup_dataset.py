import os
import sys
import zipfile
import shutil
import subprocess
from utils import load_dotenv

try:
    import gdown
except ImportError:
    print("Error: 'gdown' is not installed in the current environment.")
    print("Please make sure you are using the 'capstone' conda environment.")
    sys.exit(1)

# Load environment configurations
load_dotenv()

# Google Drive File IDs
DRIVE_IDS = {
    "OB": os.environ.get("GDRIVE_ID_OB", ""),
    "SS": os.environ.get("GDRIVE_ID_SS", ""),
    "TT": os.environ.get("GDRIVE_ID_TT", "")
}

COMPRESS_DIR = os.environ.get("COMPRESS_DIR", "compress")
RAW_DIR = os.environ.get("RAW_DIR", "raw")

def download_file(sys_name, file_id):
    """
    Download zip file from Google Drive using gdown.
    """
    os.makedirs(COMPRESS_DIR, exist_ok=True)
    output_path = os.path.join(COMPRESS_DIR, f"RE3-{sys_name}.zip")
    
    print(f"\n>>> Downloading RE3-{sys_name}.zip from Google Drive...")
    
    # Check if file already exists
    if os.path.exists(output_path):
        print(f"File {output_path} already exists. Skipping download.")
        return output_path
        
    try:
        # Call gdown download
        gdown.download(id=file_id, output=output_path, quiet=False)
        print(f"Successfully downloaded to {output_path}")
    except Exception as e:
        print(f"Error downloading RE3-{sys_name}: {e}")
        sys.exit(1)
        
    return output_path

def smart_extract(zip_path, sys_name):
    """
    Extract zip file safely and organize into raw/RE3-<sys_name>/
    """
    target_dir = os.path.join(RAW_DIR, f"RE3-{sys_name}")
    print(f"\n>>> Extracting {zip_path} to {target_dir}...")
    
    # Clean target directory if it exists
    if os.path.exists(target_dir):
        print(f"Cleaning existing directory {target_dir}...")
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    
    # Create a temporary extraction directory
    temp_extract_dir = os.path.join(RAW_DIR, f"temp_{sys_name}")
    if os.path.exists(temp_extract_dir):
        shutil.rmtree(temp_extract_dir)
    os.makedirs(temp_extract_dir, exist_ok=True)
    
    try:
        # Unzip to temporary directory
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_extract_dir)
            
        # Check structure inside temp directory
        extracted_items = os.listdir(temp_extract_dir)
        
        # If there is a single directory with the same name as the system, e.g. RE3-OB
        # we move its contents. Otherwise, we move all contents from temp.
        matching_subdirs = [d for d in extracted_items if d == f"RE3-{sys_name}" and os.path.isdir(os.path.join(temp_extract_dir, d))]
        
        if matching_subdirs:
            source_dir = os.path.join(temp_extract_dir, matching_subdirs[0])
            print(f"Detected nested directory: {matching_subdirs[0]}. Moving contents...")
        else:
            source_dir = temp_extract_dir
            print("No nested matching directory detected. Moving contents directly...")
            
        # Move all contents of source_dir to target_dir
        for item in os.listdir(source_dir):
            shutil.move(os.path.join(source_dir, item), os.path.join(target_dir, item))
            
        print(f"Extraction completed successfully for RE3-{sys_name}.")
        
    except Exception as e:
        print(f"Error extracting {zip_path}: {e}")
        sys.exit(1)
    finally:
        # Clean up temporary directory
        if os.path.exists(temp_extract_dir):
            shutil.rmtree(temp_extract_dir)

def run_split_script():
    """
    Run split_dataset.py script to recreate the dataset splits.
    """
    split_script = os.path.join("test-pipeline", "split_dataset.py")
    if not os.path.exists(split_script):
        print(f"\nWarning: Split script not found at {split_script}. Cannot recreate splits.")
        return
        
    print("\n>>> Re-running split_dataset.py to generate dataset splits...")
    try:
        # Run using python3 directly as we are already running inside conda env
        result = subprocess.run([sys.executable, split_script], check=True, capture_output=True, text=True)
        print(result.stdout)
    except subprocess.CalledProcessError as e:
        print(f"Error running split_dataset.py: {e.stderr}")
        sys.exit(1)

def main():
    print("=" * 60)
    print("     RE3 DATASET DOWNLOAD & SETUP PIPELINE")
    print("=" * 60)
    
    # Step 1 & 2: Download and extract for each system
    for sys_name, file_id in DRIVE_IDS.items():
        zip_path = download_file(sys_name, file_id)
        smart_extract(zip_path, sys_name)
        
    # Step 3: Run dataset split script to regenerate splits
    run_split_script()
    
    print("\n" + "=" * 60)
    print("     DATASET SETUP & SPLITTING COMPLETED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    main()
