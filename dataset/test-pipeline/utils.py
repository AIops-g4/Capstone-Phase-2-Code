import os

def load_dotenv(dotenv_path=".env"):
    """
    Lightweight, dependency-free .env loader to populate os.environ.
    Searches parent directories up to 3 levels.
    """
    path = dotenv_path
    for _ in range(3):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        # Ignore comments and empty lines
                        if line and not line.startswith("#") and "=" in line:
                            key, val = line.split("=", 1)
                            os.environ[key.strip()] = val.strip()
                return True
            except Exception as e:
                print(f"Warning: Failed to parse {path}: {e}")
        path = os.path.join("..", path)
    return False
