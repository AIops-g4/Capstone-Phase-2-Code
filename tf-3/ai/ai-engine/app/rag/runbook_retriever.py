import json
import os
from typing import List, Dict, Any

class RunbookRetriever:
    """
    Component 2: RAG Retriever for Operational Runbooks.
    Currently uses in-memory JSON filtering.
    """
    def __init__(self, file_path: str = "runbooks/runbooks.json"):
        self.file_path = file_path
        self._runbooks = []
        self._load()

    def _load(self):
        # Look for the file relative to the project root
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        full_path = os.path.join(base_dir, self.file_path)
        
        if os.path.exists(full_path):
            with open(full_path, 'r', encoding='utf-8') as f:
                self._runbooks = json.load(f)
        else:
            # Fallback for testing
            self._runbooks = [
                {
                    "id": "DatabaseConnectionRecoveryRunbook",
                    "description": "Recovers database connection issues by patching memory limits and restarting the deployment.",
                    "action": "PATCH_MEMORY_LIMIT"
                },
                {
                    "id": "PodOOMKilledRunbook",
                    "description": "Restarts deployment when a pod hits OOMKilled.",
                    "action": "RESTART_DEPLOYMENT"
                }
            ]

    def retrieve_relevant(self, fault_type: str, limit: int = 3) -> List[Dict[str, Any]]:
        """
        Returns relevant runbooks for the LLM to choose from.
        For Phase 2, we return all runbooks as context since the library is small.
        """
        return self._runbooks[:limit]
