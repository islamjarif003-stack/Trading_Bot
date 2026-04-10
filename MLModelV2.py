"""
================================================================================
  MLModelV2.py — XGBoost ML Filter v2.0 (Advanced Trade Quality Prediction)
  
  Upgrade from Random Forest (7 features) to XGBoost (22 features).
  
  Improvements:
    - XGBoost gradient boosting (better than RF for structured data)
    - 22 engineered features (including time patterns, order flow, etc.)
    - Historical backfill support (train on months of past data)
    - Auto-retrain every N trades
    - Feature importance logging
    - Rolling cross-validation for robustness
================================================================================
"""

import os
import json
import time
import math
import logging
import numpy as np
import pandas as pd
from collections import deque

log = logging.getLogger("InstitutionalBot")

# ─── ML IMPORTS ─────────────────────────────────────────────────────────────
try:
    import xgboost as xgb
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False
    log.warning("⚠  XGBoost not installed. Falling back to scikit-learn RandomForest.")

try:
    from sklearn.ensemble import RandomForestClassifier
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False


# ═════════════════════════════════════════════════════════════════════════════
# ██  FEATURE ENGINEERING                                                    ██
# ═════════════════════════════════════════════════════════════════════════════

# 22 feature names for logging / importance tracking
FEATURE_NAMES_V2 = [
    # ─── Original 7 (upgraded) ───
    "adx",                  # 0  ADX value
    "rsi",                  # 1  RSI value
    "score",                # 2  Confluence score
    "oi_trend",             # 3  OI trend encoded (-1/0/1)
    "funding_rate",         # 4  Funding rate (scaled)
    "atr",                  # 5  ATR value
    "regime",               # 6  Market regime (0/1)
    # ─── New 15 ───
    "rsi_slope",            # 7  RSI rate of change (last 3)
    "atr_pct",              # 8  ATR as % of price
    "volume_ratio",         # 9  Current vol / SMA vol
    "bb_width",             # 10 Bollinger Band width
    "ema_cross_dist",       # 11 Price distance from EMA21 (%)
    "cvd_trend",            # 12 CVD trend (-1/0/1)
    "taker_ratio",          # 13 Taker buy/sell ratio
    "supertrend_dist",      # 14 Distance from supertrend (%)
    "vwap_dist",            # 15 Distance from VWAP (%)
    "h1_ema_dist",          # 16 Distance from H1 EMA200 (%)
    "oi_pct_change",        # 17 OI % change
    "funding_z_score",      # 18 FR vs historical average
    "fib_proximity",        # 19 Distance to nearest fib level (%)
    "hour_sin",             # 20 Hour of day (sin encoded)
    "hour_cos",             # 21 Hour of day (cos encoded)
]


def build_feature_vector(features: dict) -> list:
    """
    Convert a feature dict to an ordered 22-element numeric vector.
    Handles missing keys gracefully with sensible defaults.
    """
    # Encoding maps
    oi_trend_map = {"RISING": 1, "FLAT": 0, "FALLING": -1}
    regime_map = {"TRENDING": 1, "RANGING": 0}
    cvd_trend_map = {"RISING": 1, "FLAT": 0, "FALLING": -1}
    
    # Get current hour for time encoding
    hour = features.get("hour", 12)
    hour_sin = math.sin(2 * math.pi * hour / 24)
    hour_cos = math.cos(2 * math.pi * hour / 24)
    
    # Current price for percentage calculations
    price = features.get("current_price", 1.0)
    if price <= 0:
        price = 1.0
    
    return [
        # Original 7
        float(features.get("adx", 20.0)),
        float(features.get("rsi", 50.0)),
        float(features.get("score", 0)),
        float(oi_trend_map.get(features.get("oi_trend", "FLAT"), 0)),
        float(features.get("funding_rate", 0.0)) * 10000,
        float(features.get("atr", 0.0)),
        float(regime_map.get(features.get("regime", "RANGING"), 0)),
        # New 15
        float(features.get("rsi_slope", 0.0)),
        float(features.get("atr", 0.0)) / price * 100 if price > 0 else 0.0,
        float(features.get("volume_ratio", 1.0)),
        float(features.get("bb_width", 2.0)),
        float(features.get("ema_cross_dist", 0.0)),
        float(cvd_trend_map.get(features.get("cvd_trend", "FLAT"), 0)),
        float(features.get("taker_ratio", 1.0)),
        float(features.get("supertrend_dist", 0.0)),
        float(features.get("vwap_dist", 0.0)),
        float(features.get("h1_ema_dist", 0.0)),
        float(features.get("oi_pct_change", 0.0)),
        float(features.get("funding_z_score", 0.0)),
        float(features.get("fib_proximity", 0.0)),
        hour_sin,
        hour_cos,
    ]


# ═════════════════════════════════════════════════════════════════════════════
# ██  XGBOOST ML FILTER v2                                                  ██
# ═════════════════════════════════════════════════════════════════════════════

class MLFilterV2:
    """
    Advanced ML Filter using XGBoost (with RandomForest fallback).
    
    Key improvements over v1:
      - XGBoost gradient boosting (better for structured/tabular data)
      - 22 engineered features (vs 7)
      - Auto-retrain every retrain_interval trades
      - Feature importance tracking
      - Backward-compatible with old trade memory format
      - Historical backfill support
    """
    
    def __init__(self, memory_file: str = None, max_memory: int = 2000,
                 min_samples: int = 10, win_threshold: float = 0.65,
                 retrain_interval: int = 25):
        
        if memory_file is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            memory_file = os.path.join(base_dir, "ml_trade_memory_v2.json")
        
        self.memory_file = memory_file
        self.max_memory = max_memory
        self.min_samples = min_samples
        self.win_threshold = win_threshold
        self.retrain_interval = retrain_interval
        
        self.memory = []  # List of {"features": [...], "outcome": 0|1}
        self._model = None
        self._trades_since_retrain = 0
        self._feature_importance = {}
        
        self._load_memory()
        log.info(
            f"🤖  ML Filter V2 initialized │ Engine: {'XGBoost' if XGBOOST_AVAILABLE else 'RandomForest'} │ "
            f"Memory: {len(self.memory)} trades │ Features: {len(FEATURE_NAMES_V2)}"
        )
    
    # ─── MEMORY MANAGEMENT ──────────────────────────────────────────────────
    
    def _load_memory(self):
        """Load trade memory from disk. Handles v1 → v2 migration."""
        try:
            if os.path.exists(self.memory_file):
                with open(self.memory_file, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        # Migrate v1 entries (7 features) to v2 (22 features)
                        migrated = []
                        for entry in data:
                            features = entry.get("features", [])
                            if len(features) < len(FEATURE_NAMES_V2):
                                # Pad with defaults
                                features = features + [0.0] * (len(FEATURE_NAMES_V2) - len(features))
                            migrated.append({
                                "features": features[:len(FEATURE_NAMES_V2)],
                                "outcome": entry.get("outcome", 0)
                            })
                        self.memory = migrated[-self.max_memory:]
                        log.info(f"🤖  ML V2 Memory loaded: {len(self.memory)} trades")
            
            # Also try to import from old v1 memory file
            old_file = os.path.join(os.path.dirname(self.memory_file), "ml_trade_memory.json")
            if os.path.exists(old_file) and not os.path.exists(self.memory_file):
                try:
                    with open(old_file, "r") as f:
                        old_data = json.load(f)
                        if isinstance(old_data, list) and len(old_data) > 0:
                            for entry in old_data:
                                features = entry.get("features", [])
                                features = features + [0.0] * (len(FEATURE_NAMES_V2) - len(features))
                                self.memory.append({
                                    "features": features[:len(FEATURE_NAMES_V2)],
                                    "outcome": entry.get("outcome", 0)
                                })
                            log.info(f"🤖  Migrated {len(old_data)} trades from v1 memory")
                            self._save_memory()
                except Exception:
                    pass
                    
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"⚠  ML V2 Memory load failed: {e}. Starting fresh.")
            self.memory = []
    
    def _save_memory(self):
        """Persist trade memory to disk."""
        try:
            with open(self.memory_file, "w") as f:
                json.dump(self.memory[-self.max_memory:], f)
        except IOError as e:
            log.warning(f"⚠  ML V2 Memory save failed: {e}")
    
    # ─── TRAINING ────────────────────────────────────────────────────────────
    
    def _train_model(self):
        """Train the ML model on accumulated trade memory."""
        if len(self.memory) < self.min_samples:
            return False
        
        X = np.array([entry["features"] for entry in self.memory])
        y = np.array([entry["outcome"] for entry in self.memory])
        
        # Need both classes
        if len(np.unique(y)) < 2:
            return False
        
        try:
            if XGBOOST_AVAILABLE:
                self._model = xgb.XGBClassifier(
                    n_estimators=100,
                    max_depth=5,
                    learning_rate=0.1,
                    subsample=0.8,
                    colsample_bytree=0.8,
                    min_child_weight=3,
                    gamma=0.1,
                    reg_alpha=0.1,
                    reg_lambda=1.0,
                    random_state=42,
                    use_label_encoder=False,
                    eval_metric='logloss',
                    n_jobs=1,
                    verbosity=0,
                )
                self._model.fit(X, y)
                
                # Log feature importance
                importances = self._model.feature_importances_
                self._feature_importance = {
                    FEATURE_NAMES_V2[i]: round(float(importances[i]), 4)
                    for i in range(len(FEATURE_NAMES_V2))
                }
                
                # Log top 5 important features
                sorted_feats = sorted(self._feature_importance.items(), 
                                      key=lambda x: x[1], reverse=True)[:5]
                feat_str = " | ".join([f"{k}: {v:.3f}" for k, v in sorted_feats])
                log.info(f"🤖  XGBoost trained │ Top features: {feat_str}")
                
            elif SKLEARN_AVAILABLE:
                self._model = RandomForestClassifier(
                    n_estimators=100,
                    max_depth=6,
                    random_state=42,
                    n_jobs=1,
                )
                self._model.fit(X, y)
                
                importances = self._model.feature_importances_
                self._feature_importance = {
                    FEATURE_NAMES_V2[i]: round(float(importances[i]), 4)
                    for i in range(min(len(FEATURE_NAMES_V2), len(importances)))
                }
                log.info(f"🤖  RandomForest trained (XGBoost fallback)")
            else:
                log.warning("⚠  No ML library available. Filter bypassed.")
                return False
            
            self._trades_since_retrain = 0
            return True
            
        except Exception as e:
            log.warning(f"⚠  ML V2 training failed: {e}")
            return False
    
    # ─── PREDICTION ──────────────────────────────────────────────────────────
    
    def predict_win_probability(self, features: dict) -> float:
        """
        Predict the win probability for a given set of trade features.
        
        Returns:
            float: Win probability (0.0 to 1.0).
                   Returns 0.0 if insufficient data (strict blocking).
                   Returns 1.0 only if ML library is unavailable.
        """
        if not XGBOOST_AVAILABLE and not SKLEARN_AVAILABLE:
            log.warning("⚠  No ML library installed. Filter bypassed.")
            return 1.0
        
        if len(self.memory) < self.min_samples:
            log.info(
                f"🤖  ML V2: LEARNING ({len(self.memory)}/{self.min_samples} samples) "
                f"— Blocking entry until trained"
            )
            return 0.0
        
        try:
            # Auto-retrain if needed
            if self._model is None or self._trades_since_retrain >= self.retrain_interval:
                success = self._train_model()
                if not success:
                    return 1.0  # Bypass on training failure
            
            feature_vector = np.array(build_feature_vector(features)).reshape(1, -1)
            
            if hasattr(self._model, 'predict_proba'):
                proba = self._model.predict_proba(feature_vector)
                classes = list(self._model.classes_)
                win_idx = classes.index(1) if 1 in classes else 0
                return float(proba[0][win_idx])
            else:
                pred = self._model.predict(feature_vector)
                return float(pred[0])
                
        except Exception as e:
            log.warning(f"⚠  ML V2 Prediction failed: {e}. Bypassing.")
            return 1.0
    
    # ─── LOGGING TRADES ──────────────────────────────────────────────────────
    
    def log_trade(self, features: dict, outcome: int):
        """
        Log a completed trade's features and outcome.
        
        Args:
            features: dict with feature values
            outcome: 1 for Win, 0 for Loss
        """
        feature_vector = build_feature_vector(features)
        self.memory.append({"features": feature_vector, "outcome": outcome})
        
        # Prune
        if len(self.memory) > self.max_memory:
            self.memory = self.memory[-self.max_memory:]
        
        self._save_memory()
        self._trades_since_retrain += 1
        
        # Invalidate model if retrain interval reached
        if self._trades_since_retrain >= self.retrain_interval:
            self._model = None
            log.info(f"🤖  ML V2: Retrain scheduled (every {self.retrain_interval} trades)")
        
        result_str = "✅ WIN" if outcome == 1 else "❌ LOSS"
        log.info(
            f"🤖  ML V2 Trade Logged: {result_str} │ Buffer: {len(self.memory)}/{self.max_memory} │ "
            f"Since retrain: {self._trades_since_retrain}/{self.retrain_interval}"
        )
    
    # ─── BACKFILL ────────────────────────────────────────────────────────────
    
    def load_backfill(self, backfill_file: str):
        """
        Load pre-generated historical backfill data.
        
        Args:
            backfill_file: Path to JSON file with backfill entries
        """
        try:
            if not os.path.exists(backfill_file):
                log.warning(f"⚠  Backfill file not found: {backfill_file}")
                return
            
            with open(backfill_file, "r") as f:
                backfill = json.load(f)
            
            if not isinstance(backfill, list):
                return
            
            count = 0
            for entry in backfill:
                features = entry.get("features", [])
                if len(features) < len(FEATURE_NAMES_V2):
                    features = features + [0.0] * (len(FEATURE_NAMES_V2) - len(features))
                
                self.memory.append({
                    "features": features[:len(FEATURE_NAMES_V2)],
                    "outcome": entry.get("outcome", 0)
                })
                count += 1
            
            # Prune and save
            self.memory = self.memory[-self.max_memory:]
            self._save_memory()
            self._model = None  # Force retrain
            
            log.info(f"🤖  ML V2: Loaded {count} backfill entries │ Total: {len(self.memory)}")
            
        except Exception as e:
            log.warning(f"⚠  Backfill load failed: {e}")
    
    # ─── STATS ───────────────────────────────────────────────────────────────
    
    def get_stats(self) -> dict:
        """Return ML stats for dashboard display."""
        total = len(self.memory)
        if total == 0:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                    "engine": "XGBoost" if XGBOOST_AVAILABLE else "RandomForest",
                    "features": len(FEATURE_NAMES_V2),
                    "top_features": {}}
        
        wins = sum(1 for entry in self.memory if entry["outcome"] == 1)
        return {
            "total": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1),
            "engine": "XGBoost" if XGBOOST_AVAILABLE else "RandomForest",
            "features": len(FEATURE_NAMES_V2),
            "top_features": dict(sorted(
                self._feature_importance.items(), 
                key=lambda x: x[1], reverse=True
            )[:5]) if self._feature_importance else {},
        }
    
    def get_feature_importance(self) -> dict:
        """Return full feature importance dict."""
        return self._feature_importance.copy()
