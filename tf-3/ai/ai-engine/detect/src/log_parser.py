import os
import re
import pandas as pd
import numpy as np
from drain3.template_miner import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from .config import DRAIN_SIM_TH, DRAIN_DEPTH, LOG_ERROR_KEYWORDS

class Drain3LogParser:
    """
    Log Parser using Drain3. Clusters raw log messages into templates and
    aggregates template counts into timeseries.
    """
    def __init__(self, service_aware=True):
        self.service_aware = service_aware
        
        # Configure Drain3 programmatically
        config = TemplateMinerConfig()
        config.drain_sim_th = DRAIN_SIM_TH
        config.drain_depth = DRAIN_DEPTH
        config.masking = [
            {"regex_pattern": r"\d+\.\d+\.\d+\.\d+", "mask_with": "IP"},
            {"regex_pattern": r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}", "mask_with": "UUID"},
            {"regex_pattern": r"\d+", "mask_with": "NUM"}
        ]
        
        self.miner = TemplateMiner(config=config)
        self.error_keywords = re.compile(LOG_ERROR_KEYWORDS, re.IGNORECASE)
        self.template_to_error_flag = {}

    def parse_logs(self, df_logs: pd.DataFrame, time_start: int, time_end: int):
        """
        Parse logs from the dataframe and aggregate template frequencies per second.
        
        Parameters:
        - df_logs: DataFrame containing application logs.
        - time_start: start Unix timestamp (seconds).
        - time_end: end Unix timestamp (seconds).
        
        Returns:
        - df_log_ts: DataFrame of shape (time_steps, num_templates) representing template counts over time.
        - template_info: Dict mapping template_id to template details (pattern, error_flag).
        """
        print(f"Parsing {len(df_logs)} log lines using Drain3...")
        
        # 1. Convert nanosecond timestamps to second-level timestamps
        # If timestamp is missing or invalid, fallback to mapping time or filling
        df_logs = df_logs.copy()
        if "timestamp" in df_logs.columns:
            # Nanosecond timestamp -> Second-level timestamp
            df_logs["time_sec"] = (df_logs["timestamp"] // 1000000000).astype(int)
        else:
            # If no nanosecond timestamp, generate from time or time_sec
            df_logs["time_sec"] = time_start
            
        # Filter logs within our metrics window
        df_logs = df_logs[(df_logs["time_sec"] >= time_start) & (df_logs["time_sec"] <= time_end)]
        
        # 2. Feed messages to Drain3 and map each log line to a template ID
        log_records = []
        for idx, row in df_logs.iterrows():
            container = row.get("container_name", "unknown")
            msg = str(row.get("message", ""))
            level = str(row.get("level", "")).lower()
            
            # Combine container name and message to make it service-aware
            if self.service_aware:
                full_message = f"{container} {msg}"
            else:
                full_message = msg
                
            # Miner parses the log message
            result = self.miner.add_log_message(full_message)
            template_id = result["cluster_id"]
            
            # Record log template details
            if template_id not in self.template_to_error_flag:
                template_pattern = result["template_mined"]
                # Mark as error template if it contains error keywords or if log level is error/warn
                is_err = bool(self.error_keywords.search(template_pattern)) or level in ["error", "warning", "fatal", "warn"]
                self.template_to_error_flag[template_id] = {
                    "pattern": template_pattern,
                    "is_error": is_err,
                    "container": container
                }
                
            log_records.append({
                "time_sec": int(row["time_sec"]),
                "template_id": template_id,
                "container": container
            })
            
        df_parsed = pd.DataFrame(log_records)
        
        # 3. Aggregate frequencies in second-level bins
        time_range = np.arange(time_start, time_end + 1)
        unique_templates = sorted(list(self.template_to_error_flag.keys()))
        
        # Create a base dataframe with all seconds in the range
        df_aggregated = pd.DataFrame({"time": time_range})
        
        if len(df_parsed) > 0:
            # Pivot table to count template occurrences per second
            pivot = df_parsed.pivot_table(
                index="time_sec", 
                columns="template_id", 
                aggfunc="size", 
                fill_value=0
            )
            
            # Reindex to match the exact time range and all unique templates
            pivot = pivot.reindex(index=time_range, columns=unique_templates, fill_value=0)
            pivot.index.name = "time"
            pivot = pivot.reset_index()
            
            # Combine with our base dataframe
            df_log_ts = pd.merge(df_aggregated, pivot, on="time", how="left").fillna(0)
        else:
            # If no logs, return empty frequencies (all zeros)
            for t_id in unique_templates:
                df_aggregated[t_id] = 0
            df_log_ts = df_aggregated
            
        # Ensure all columns are clean
        # Rename template columns to string format like 'template_1', 'template_2', etc.
        template_cols = [col for col in df_log_ts.columns if isinstance(col, int)]
        rename_dict = {col: f"template_{col}" for col in template_cols}
        df_log_ts = df_log_ts.rename(columns=rename_dict)
        
        # Rename template_info keys to match new column names
        template_info = {f"template_{k}": v for k, v in self.template_to_error_flag.items()}
        
        print(f"Extraction complete. Found {len(template_info)} unique log templates.")
        return df_log_ts, template_info
