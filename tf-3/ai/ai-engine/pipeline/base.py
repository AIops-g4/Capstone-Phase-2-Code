from abc import ABC, abstractmethod

class BaseIngestor(ABC):
    """
    Abstract base class for ingesting and preprocessing raw dataset files.
    Converts raw CSV logs, metrics, and traces into a standard Telemetry Window.
    """
    
    @abstractmethod
    def ingest(self, case_path: str, tenant_id: str, system: str, inject_time: int) -> list:
        """
        Reads files from a case folder and outputs a list of TelemetryDataPoint dicts
        conforming to the TelemetryDataPoint schema.
        """
        pass


class BaseDetector(ABC):
    """
    Abstract base class for anomaly detection.
    Evaluates the telemetry window and determines if there is an anomaly.
    """
    
    @abstractmethod
    def detect(self, telemetry_window: list, correlation_id: str) -> dict:
        """
        Processes a telemetry window and returns a DetectResponse dict
        conforming to the DetectResponse schema.
        """
        pass


class BaseDecider(ABC):
    """
    Abstract base class for root cause localization, runbook matching,
    and action plan recommendation.
    """
    
    @abstractmethod
    def decide(self, anomaly_context: dict, correlation_id: str, idempotency_key: str, dry_run_mode: bool) -> dict:
        """
        Matches an anomaly context against a runbook repository to recommend remediation.
        Returns a DecideResponse dict conforming to the DecideResponse schema.
        """
        pass


class BaseVerifier(ABC):
    """
    Abstract base class for post-remediation verification.
    """
    
    @abstractmethod
    def verify(self, action_executed: dict, post_telemetry_window: list, correlation_id: str, idempotency_key: str, dry_run_mode: bool) -> dict:
        """
        Evaluates the telemetry window after action execution to verify recovery.
        Returns a VerifyResponse dict conforming to the VerifyResponse schema.
        """
        pass
