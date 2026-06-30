import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from .config import (
    IFOREST_MULTIVARIATE_THRESHOLD_MULTIPLIER,
    IFOREST_UNIVARIATE_THRESHOLD_MULTIPLIER,
    EWMA_ALPHA,
    EWMA_THRESHOLD,
    BASELINE_LENGTH,
    USE_RRCF,
    USE_BOCPD,
    RRCF_NUM_TREES,
    RRCF_TREE_SIZE,
    RRCF_MULTIVARIATE_THRESHOLD_MULTIPLIER,
    RRCF_UNIVARIATE_THRESHOLD_MULTIPLIER,
    IFOREST_CONTAMINATION,
    IFOREST_N_ESTIMATORS,
    RANDOM_STATE,
    BOCPD_HAZARD
)


# --- Robust Random Cut Forest (RRCF) Monkey-Patch ---
# The official rrcf library contains bugs where it crashes with:
# 1. ValueError/NaN choice when trying to split a subset of identical points or if the dataset has only 1 unique point.
# 2. ValueError when a cut fails to partition points due to duplicate values or float precision.
# 3. AttributeError (NoneType sibling) in codisp when a tree is left with None children due to unhandled split failures.
# This monkey-patch implements a robust recursive tree construction method that solves all these edge cases.
try:
    import rrcf
    from rrcf.rrcf import Leaf, Branch

    def _robust_mktree(self, X, S, N, I, parent=None, side='root', depth=0):
        # Increment depth as we traverse down
        depth += 1
        
        # Case 1: The subset has only 1 point. This can only happen at the root of the tree
        # if the entire dataset contains only 1 unique point after duplicate removal.
        if S.sum() == 1:
            i = np.flatnonzero(S).item()
            leaf = Leaf(i=i, d=depth, u=parent, x=X[i, :], n=N[i])
            if side == 'root':
                self.root = leaf
            elif side == 'l':
                parent.l = leaf
            elif side == 'r':
                parent.r = leaf
                
            if I is not None:
                J = np.flatnonzero(I == i)
                J = self.index_labels[J]
                for j in J:
                    self.leaves[j] = leaf
            else:
                i = self.index_labels[i]
                self.leaves[i] = leaf
            return

        # Case 2: The subset has multiple points.
        xmax = X[S].max(axis=0)
        xmin = X[S].min(axis=0)
        
        # Check if all points in the subset are identical across all dimensions
        if (xmax - xmin).sum() == 0:
            # All points are identical. We cannot perform a spatial split.
            # We manually create a Branch and force a clean index-based split.
            q = 0
            p = xmin[0]
            branch = Branch(q=q, p=p, u=parent)
            if side == 'root':
                self.root = branch
            elif side == 'l':
                parent.l = branch
            elif side == 'r':
                parent.r = branch
                
            indices = np.flatnonzero(S)
            mid = len(indices) // 2
            S1 = np.zeros_like(S)
            S1[indices[:mid]] = True
            S2 = np.zeros_like(S)
            S2[indices[mid:]] = True
        else:
            # Standard cut
            S1, S2, branch = self._cut(X, S, parent=parent, side=side)
            if side == 'root':
                self.root = branch
            
            # If the cut failed to partition the points (due to float precision),
            # force a clean index-based split to guarantee both subsets are non-empty.
            if S1.sum() == 0 or S2.sum() == 0:
                indices = np.flatnonzero(S)
                mid = len(indices) // 2
                S1 = np.zeros_like(S)
                S1[indices[:mid]] = True
                S2 = np.zeros_like(S)
                S2[indices[mid:]] = True

        # Recursively build left subtree
        if S1.sum() > 1:
            self._mktree(X, S1, N, I, parent=branch, side='l', depth=depth)
        else:
            i = np.flatnonzero(S1).item()
            leaf = Leaf(i=i, d=depth, u=branch, x=X[i, :], n=N[i])
            branch.l = leaf
            if I is not None:
                J = np.flatnonzero(I == i)
                J = self.index_labels[J]
                for j in J:
                    self.leaves[j] = leaf
            else:
                i = self.index_labels[i]
                self.leaves[i] = leaf

        # Recursively build right subtree
        if S2.sum() > 1:
            self._mktree(X, S2, N, I, parent=branch, side='r', depth=depth)
        else:
            i = np.flatnonzero(S2).item()
            leaf = Leaf(i=i, d=depth, u=branch, x=X[i, :], n=N[i])
            branch.r = leaf
            if I is not None:
                J = np.flatnonzero(I == i)
                J = self.index_labels[J]
                for j in J:
                    self.leaves[j] = leaf
            else:
                i = self.index_labels[i]
                self.leaves[i] = leaf

        depth -= 1

    rrcf.RCTree._mktree = _robust_mktree
    print("  [RRCF Patch] Successfully applied robust RCTree._mktree patch.")
except Exception as e:
    print(f"  [RRCF Patch] Warning: Failed to apply rrcf safety patch: {e}")


class BaseDetector:
    """
    Abstract interface for all anomaly detector engines.
    """
    def fit(self, df_baseline: pd.DataFrame) -> None:
        pass

    def detect(self, df: pd.DataFrame) -> tuple:
        raise NotImplementedError("Detect method must be implemented by subclasses.")


class EWMAAnomalyDetector(BaseDetector):
    """
    Exponentially Weighted Moving Average (EWMA) Anomaly Detector for univariate time series.
    Suitable for service level indicators like latency and error rates.
    """
    def __init__(self, alpha=EWMA_ALPHA, threshold=EWMA_THRESHOLD):
        self.alpha = alpha
        self.threshold = threshold

    def detect_series(self, series: pd.Series, baseline_len: int = BASELINE_LENGTH):
        # Calculate EWMA
        ewma = series.ewm(alpha=self.alpha, adjust=False).mean()
        
        # Calculate residuals
        residuals = series - ewma
        
        # Compute standard deviation on baseline period
        baseline_residuals = residuals.iloc[:baseline_len]
        std = baseline_residuals.std()
        if pd.isna(std) or std == 0:
            std = 1e-6  # Prevent division by zero
            
        # Anomaly if residual exceeds threshold * std
        anomalies = np.abs(residuals) > self.threshold * std
        scores = np.abs(residuals) / std
        
        return anomalies.values, scores.values

    def detect(self, df: pd.DataFrame) -> tuple:
        # Fallback interface for BaseDetector compatibility
        col = df.columns[0]
        return self.detect_series(df[col])


class IsolationForestDetector(BaseDetector):
    """
    Isolation Forest Anomaly Detector. Supports both univariate and multivariate inputs.
    Uses dynamic score thresholding based on baseline mean and standard deviation to prevent false positives.
    """
    def __init__(self, threshold_multiplier=4.0, random_state=RANDOM_STATE):
        self.threshold_multiplier = threshold_multiplier
        self.random_state = random_state
        self.model = None
        self.score_threshold = 0.0

    def fit(self, df_baseline: pd.DataFrame):
        df_clean = df_baseline.fillna(0)
        self.model = IsolationForest(
            contamination=IFOREST_CONTAMINATION,
            random_state=self.random_state,
            n_estimators=IFOREST_N_ESTIMATORS
        )
        self.model.fit(df_clean)
        
        # Calibrate threshold on baseline scores
        baseline_scores = -self.model.decision_function(df_clean)
        mean_score = np.mean(baseline_scores)
        std_score = np.std(baseline_scores)
        self.score_threshold = mean_score + self.threshold_multiplier * std_score
        print(f"  [IForest Calibration] Baseline score mean: {mean_score:.4f}, std: {std_score:.4f}. Threshold set to: {self.score_threshold:.4f}")

    def detect(self, df: pd.DataFrame):
        if self.model is None:
            raise ValueError("Model must be fitted before detection.")
            
        df_clean = df.fillna(0)
        scores = -self.model.decision_function(df_clean)
        anomalies = scores > self.score_threshold
        
        return anomalies, scores


class RRCFDetector(BaseDetector):
    """
    Robust Random Cut Forest (RRCF) Anomaly Detector. Supports both univariate and multivariate inputs.
    Uses dynamic score thresholding based on baseline mean and standard deviation to prevent false positives.
    """
    def __init__(self, threshold_multiplier=4.0, num_trees=40, tree_size=128, random_state=RANDOM_STATE):
        self.threshold_multiplier = threshold_multiplier
        self.num_trees = num_trees
        self.tree_size = tree_size
        self.random_state = random_state
        self.score_threshold = 0.0
        self.is_fitted = False

    def fit(self, df_baseline: pd.DataFrame):
        df_clean = df_baseline.fillna(0)
        X = df_clean.values.astype(np.float64)
        
        self.is_fitted = True
        baseline_scores = self._compute_scores(X)
        mean_score = np.mean(baseline_scores)
        std_score = np.std(baseline_scores)
        
        regularized_std = max(std_score, 1e-4)
        self.score_threshold = mean_score + self.threshold_multiplier * regularized_std
        print(f"  [RRCF Calibration] Baseline score mean: {mean_score:.4f}, std: {std_score:.4f}. Threshold set to: {self.score_threshold:.4f}")

    def detect(self, df: pd.DataFrame):
        if not self.is_fitted:
            raise ValueError("Model must be fitted before detection.")
            
        df_clean = df.fillna(0)
        X = df_clean.values.astype(np.float64)
        scores = self._compute_scores(X)
        anomalies = scores > self.score_threshold
        
        return anomalies, scores

    def _compute_scores(self, X: np.ndarray) -> np.ndarray:
        import rrcf
        X_float = X.astype(np.float64).copy()
        
        stds = np.std(X_float, axis=0)
        means = np.mean(np.abs(X_float), axis=0)
        scale = np.nan_to_num(stds, nan=0.0) + 1e-5 * (np.nan_to_num(means, nan=0.0) + 1.0)
        rng = np.random.default_rng(self.random_state)
        X_float += rng.normal(0, 1e-6, size=X_float.shape) * scale
        
        n = X_float.shape[0]
        tree_size = self.tree_size
        num_trees = self.num_trees
        
        if n < tree_size:
            tree_size = n
            
        np.random.seed(self.random_state)
        forest = []
        if n == 0 or tree_size == 0:
            return np.zeros(n)
            
        while len(forest) < num_trees:
            if n // tree_size > 0:
                ixs = np.random.choice(n, size=(n // tree_size, tree_size), replace=False)
                trees = [rrcf.RCTree(X_float[ix], index_labels=ix) for ix in ixs]
                forest.extend(trees)
            else:
                ix = np.arange(n)
                np.random.shuffle(ix)
                tree = rrcf.RCTree(X_float[ix], index_labels=ix)
                forest.append(tree)
                 
        avg_codisp = pd.Series(0.0, index=np.arange(n))
        index = np.zeros(n)
        for tree in forest:
            codisp = pd.Series({leaf : tree.codisp(leaf) for leaf in tree.leaves})
            avg_codisp[codisp.index] += codisp
            np.add.at(index, codisp.index.values, 1)
            
        nonzero = index > 0
        avg_codisp[nonzero] /= index[nonzero]
        
        return avg_codisp.values


class BOCPDDetector(BaseDetector):
    """
    Bayesian Online Change Point Detection (BOCPD) wrapper for anomaly detection.
    Filters out latency/error columns to run purely on resource metrics.
    """
    def __init__(self):
        self.is_fitted = False

    def fit(self, df_baseline: pd.DataFrame):
        self.is_fitted = True

    def detect(self, df: pd.DataFrame):
        from functools import partial
        from baro._bocpd import online_changepoint_detection, constant_hazard, MultivariateT
        from baro.anomaly_detection import find_cps
        
        df_clean = df.fillna(0).replace([np.inf, -np.inf], np.nan).ffill().bfill().fillna(0)
        
        # 1. Filter out key performance indicators (latency and error columns) to reduce dimensionality
        selected_cols = []
        for c in df_clean.columns:
            if 'queue-master' in c or 'rabbitmq_' in c:
                continue
            c_lower = c.lower()
            if "latency" in c_lower or "error" in c_lower:
                continue
            selected_cols.append(c)
        if selected_cols:
            df_clean = df_clean[selected_cols]
            
        df_clean = df_clean.loc[:, df_clean.nunique() > 1]
        
        if df_clean.empty:
            return np.zeros(len(df), dtype=bool), np.zeros(len(df), dtype=float)
            
        for col in df_clean.columns:
            col_min = df_clean[col].min()
            col_max = df_clean[col].max()
            if col_max - col_min > 1e-6:
                df_clean[col] = (df_clean[col] - col_min) / (col_max - col_min)
            else:
                df_clean[col] = 0.0
                
        data = df_clean.to_numpy()
        
        try:
            R, maxes = online_changepoint_detection(
                data,
                partial(constant_hazard, BOCPD_HAZARD),
                MultivariateT(dims=data.shape[1])
            )
            cps = find_cps(maxes)
            anomaly_indices = [p[0] for p in cps]
        except Exception as e:
            print(f"  [BOCPD Warning] Failed to run optimized custom BOCPD: {e}. Falling back to empty anomalies.")
            anomaly_indices = []
        
        anomalies = np.zeros(len(df), dtype=bool)
        if anomaly_indices:
            for idx in anomaly_indices:
                if 0 <= idx < len(df):
                    anomalies[idx] = True
                    
        scores = anomalies.astype(float)
        return anomalies, scores


class AnomalyDetectionPipeline:
    """
    OOP pipeline that coordinates the execution of multivariate and EWMA anomaly detectors.
    """
    def __init__(self):
        pass

    def run_pipeline(self, df_metrics: pd.DataFrame, baseline_len: int = BASELINE_LENGTH) -> dict:
        df_features = df_metrics.drop(columns=["time"], errors="ignore")
        df_baseline = df_features.iloc[:baseline_len]
        
        # 1. Run Multivariate Anomaly Detection (on resource metrics)
        multivariate_cols = [c for c in df_features.columns if "latency" not in c.lower() and "error" not in c.lower()]
        df_multivariate_features = df_features[multivariate_cols]
        df_multivariate_baseline = df_baseline[multivariate_cols]
        
        # detect_decide_verify is benchmarked with BOCPD only.  Do not silently
        # fall back to Isolation Forest/RRCF in the API server path.
        mif = BOCPDDetector()
            
        mif.fit(df_multivariate_baseline)
        mif_anomalies, mif_scores = mif.detect(df_multivariate_features)
        
        # 2. Run EWMA on service-level metrics (Latency, Errors)
        univariate_results = {}
        ewma_results = {}
        
        for col in df_features.columns:
            series = df_features[col]
            
            if "latency" in col or "error" in col:
                detector = EWMAAnomalyDetector(alpha=EWMA_ALPHA, threshold=EWMA_THRESHOLD)
                anoms, scores = detector.detect_series(series, baseline_len)
                ewma_results[col] = {
                    "anomalies": anoms,
                    "scores": scores
                }
                
        return {
            "multivariate": {
                "anomalies": mif_anomalies,
                "scores": mif_scores
            },
            "univariate": univariate_results,
            "ewma": ewma_results
        }


def run_metric_anomaly_detection(df_metrics: pd.DataFrame, baseline_len: int = BASELINE_LENGTH) -> dict:
    """
    Backward-compatible wrapper function for running anomaly detection pipeline.
    """
    pipeline = AnomalyDetectionPipeline()
    return pipeline.run_pipeline(df_metrics, baseline_len)
