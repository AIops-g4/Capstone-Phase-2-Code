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
    RANDOM_STATE
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

class EWMAAnomalyDetector:
    """
    Exponentially Weighted Moving Average (EWMA) Anomaly Detector for univariate time series.
    Suitable for service level indicators like latency and error rates.
    """
    def __init__(self, alpha=EWMA_ALPHA, threshold=EWMA_THRESHOLD):
        self.alpha = alpha
        self.threshold = threshold

    def detect(self, series: pd.Series, baseline_len: int = BASELINE_LENGTH):
        """
        Detect anomalies in a series.
        baseline_len defines the initial period used to compute standard deviation baseline.
        """
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
        
        return anomalies, scores

class IsolationForestDetector:
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
        """
        Fit the Isolation Forest model on normal baseline data and calibrate the threshold.
        """
        df_clean = df_baseline.fillna(0)
        
        # Fit with a small nominal contamination
        self.model = IsolationForest(
            contamination=IFOREST_CONTAMINATION,
            random_state=self.random_state,
            n_estimators=IFOREST_N_ESTIMATORS
        )
        self.model.fit(df_clean)
        
        # Calibrate threshold on the baseline scores
        # decision_function returns negative values for outliers, positive for inliers.
        # We invert it: higher score = more anomalous
        baseline_scores = -self.model.decision_function(df_clean)
        mean_score = np.mean(baseline_scores)
        std_score = np.std(baseline_scores)
        self.score_threshold = mean_score + self.threshold_multiplier * std_score
        print(f"  [IForest Calibration] Baseline score mean: {mean_score:.4f}, std: {std_score:.4f}. Threshold set to: {self.score_threshold:.4f}")

    def detect(self, df: pd.DataFrame):
        """
        Predict anomalies on the dataset. Returns boolean anomaly flags and anomaly scores.
        """
        if self.model is None:
            raise ValueError("Model must be fitted before detection.")
            
        df_clean = df.fillna(0)
        scores = -self.model.decision_function(df_clean)
        anomalies = scores > self.score_threshold
        
        return anomalies, scores

class RRCFDetector:
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
        """
        Fit the RRCF model on normal baseline data and calibrate the threshold.
        """
        df_clean = df_baseline.fillna(0)
        X = df_clean.values.astype(np.float64)
        
        # Calibrate threshold on the baseline scores
        self.is_fitted = True
        baseline_scores = self._compute_scores(X)
        mean_score = np.mean(baseline_scores)
        std_score = np.std(baseline_scores)
        
        # Regularize standard deviation to prevent division by zero
        regularized_std = max(std_score, 1e-4)
        
        self.score_threshold = mean_score + self.threshold_multiplier * regularized_std
        print(f"  [RRCF Calibration] Baseline score mean: {mean_score:.4f}, std: {std_score:.4f}. Threshold set to: {self.score_threshold:.4f}")

    def detect(self, df: pd.DataFrame):
        """
        Predict anomalies on the dataset. Returns boolean anomaly flags and anomaly scores.
        """
        if not self.is_fitted:
            raise ValueError("Model must be fitted before detection.")
            
        df_clean = df.fillna(0)
        X = df_clean.values.astype(np.float64)
        scores = self._compute_scores(X)
        anomalies = scores > self.score_threshold
        
        return anomalies, scores

    def _compute_scores(self, X: np.ndarray) -> np.ndarray:
        """
        Computes average CoDisp anomaly scores for all points in X using the Robust Random Cut Forest.
        Implements the forest construction and CoDisp accumulation logic provided in the user's specification.
        """
        import rrcf
        X_float = X.astype(np.float64).copy()
        
        # Add scaled relative jitter to prevent duplicate rows and constant columns
        # This is a critical safety step to prevent numerical issues on flat/constant metrics
        stds = np.std(X_float, axis=0)
        means = np.mean(np.abs(X_float), axis=0)
        scale = np.nan_to_num(stds, nan=0.0) + 1e-5 * (np.nan_to_num(means, nan=0.0) + 1.0)
        rng = np.random.default_rng(self.random_state)
        X_float += rng.normal(0, 1e-6, size=X_float.shape) * scale
        
        n = X_float.shape[0]
        tree_size = self.tree_size
        num_trees = self.num_trees
        
        # Determine effective tree size (cannot exceed number of available samples)
        if n < tree_size:
            tree_size = n
            
        # Seed numpy's global RNG to ensure deterministic runs (matching user seed usage)
        np.random.seed(self.random_state)
        
        forest = []
        # If tree_size is 0 (e.g. empty dataset), return zeros
        if n == 0 or tree_size == 0:
            return np.zeros(n)
            
        # Construct forest matching user's pattern: partition-based sampling
        while len(forest) < num_trees:
            if n // tree_size > 0:
                # Select random subsets of points uniformly from point set
                ixs = np.random.choice(n, size=(n // tree_size, tree_size), replace=False)
                # Add sampled trees to forest (using index_labels=ix)
                trees = [rrcf.RCTree(X_float[ix], index_labels=ix) for ix in ixs]
                forest.extend(trees)
            else:
                # Fallback: if we have fewer points than the tree_size, sample all points as a single tree
                ix = np.arange(n)
                # Shuffle the indices to introduce random variation if multiple trees are built
                np.random.shuffle(ix)
                tree = rrcf.RCTree(X_float[ix], index_labels=ix)
                forest.append(tree)
                
        # Compute average CoDisp exactly like the user's sample code
        avg_codisp = pd.Series(0.0, index=np.arange(n))
        index = np.zeros(n)
        for tree in forest:
            codisp = pd.Series({leaf : tree.codisp(leaf) for leaf in tree.leaves})
            avg_codisp[codisp.index] += codisp
            np.add.at(index, codisp.index.values, 1)
            
        # Divide by frequency, avoiding division by zero
        nonzero = index > 0
        avg_codisp[nonzero] /= index[nonzero]
        
        return avg_codisp.values
class BOCPDDetector:
    """
    Bayesian Online Change Point Detection (BOCPD) wrapper for anomaly detection.
    """
    def __init__(self):
        self.is_fitted = False

    def fit(self, df_baseline: pd.DataFrame):
        self.is_fitted = True

    def detect(self, df: pd.DataFrame):
        from functools import partial
        from baro._bocpd import online_changepoint_detection, constant_hazard, MultivariateT
        from baro.anomaly_detection import find_cps
        
        # Clean metrics data to prevent numerical issues
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
            
        # Drop constant columns to prevent numerical singularity issues
        df_clean = df_clean.loc[:, df_clean.nunique() > 1]
        
        # If no columns left or empty dataframe, return empty
        if df_clean.empty:
            return np.zeros(len(df), dtype=bool), np.zeros(len(df), dtype=float)
            
        # Min-Max Normalization
        for col in df_clean.columns:
            col_min = df_clean[col].min()
            col_max = df_clean[col].max()
            if col_max - col_min > 1e-6:
                df_clean[col] = (df_clean[col] - col_min) / (col_max - col_min)
            else:
                df_clean[col] = 0.0
                
        data = df_clean.to_numpy()
        
        try:
            # RUN BOCPD on filtered data (1s resolution)
            R, maxes = online_changepoint_detection(
                data,
                partial(constant_hazard, 50),
                MultivariateT(dims=data.shape[1])
            )
            cps = find_cps(maxes)
            anomaly_indices = [p[0] for p in cps]
        except Exception as e:
            print(f"  [BOCPD Warning] Failed to run optimized custom BOCPD: {e}. Falling back to empty anomalies.")
            anomaly_indices = []
        
        # Convert list of anomaly indices/timesteps to a boolean array
        anomalies = np.zeros(len(df), dtype=bool)
        if anomaly_indices:
            for idx in anomaly_indices:
                if 0 <= idx < len(df):
                    anomalies[idx] = True
                    
        # Return boolean array and a dummy score (zeros/ones)
        scores = anomalies.astype(float)
        return anomalies, scores

def run_metric_anomaly_detection(df_metrics: pd.DataFrame, baseline_len: int = BASELINE_LENGTH):
    """
    Runs the comprehensive metric anomaly detection pipeline.
    1. Multivariate Anomaly Detection (Isolation Forest, RRCF, or BOCPD).
    2. Univariate Anomaly Detection on individual resource metrics (CPU, Memory, Sockets, DiskIO)
       to pinpoint which metric of which service is anomalous (Isolation Forest, RRCF, or BOCPD).
    3. EWMA on service-level metrics (Latency, Errors) to detect sudden spikes.
    
    Returns a dictionary of results.
    """
    # 1. Prepare data (exclude time column)
    df_features = df_metrics.drop(columns=["time"], errors="ignore")
    df_baseline = df_features.iloc[:baseline_len]
    
    # 2. Multivariate Anomaly Detection
    # Exclude error and latency columns from multivariate detection
    multivariate_cols = [c for c in df_features.columns if "latency" not in c.lower() and "error" not in c.lower()]
    df_multivariate_features = df_features[multivariate_cols]
    df_multivariate_baseline = df_baseline[multivariate_cols]
    
    if USE_BOCPD:
        mif = BOCPDDetector()
    elif USE_RRCF:
        mif = RRCFDetector(
            threshold_multiplier=RRCF_MULTIVARIATE_THRESHOLD_MULTIPLIER,
            num_trees=RRCF_NUM_TREES,
            tree_size=RRCF_TREE_SIZE
        )
    else:
        mif = IsolationForestDetector(threshold_multiplier=IFOREST_MULTIVARIATE_THRESHOLD_MULTIPLIER)
        
    mif.fit(df_multivariate_baseline)
    mif_anomalies, mif_scores = mif.detect(df_multivariate_features)
    
    # 3. EWMA for service-level metrics (Latency, Errors)
    univariate_results = {}
    ewma_results = {}
    
    # Identify service columns
    for col in df_features.columns:
        series = df_features[col]
        
        # Check if the column is a service level indicator (latency, error) -> use EWMA
        if "latency" in col or "error" in col:
            detector = EWMAAnomalyDetector(alpha=EWMA_ALPHA, threshold=EWMA_THRESHOLD)
            anoms, scores = detector.detect(series, baseline_len)
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
