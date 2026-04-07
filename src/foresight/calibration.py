"""Probability calibration — Platt Scaling + forecast logging.

Platt scaling corrects LLM hedging bias (systematic drift toward 0.5).
alpha=sqrt(3) is the theoretical optimal (AIA Forecaster, arXiv:2511.07678).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

PLATT_ALPHA = math.sqrt(3)


def platt_scale(p: float, alpha: float = PLATT_ALPHA) -> float:
    """Apply Platt scaling: push probabilities away from 0.5."""
    p_clamped = max(0.01, min(0.99, p))
    logit_p = math.log(p_clamped / (1.0 - p_clamped))
    return 1.0 / (1.0 + math.exp(-alpha * logit_p))


class CalibrationLogger:
    """Append-only JSONL logger for forecast-outcome pairs."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def log_forecast(
        self, question_id: str, raw_prob: float, calibrated_prob: float,
        meta: dict[str, Any] | None = None,
    ) -> None:
        record = {
            "type": "forecast",
            "question_id": question_id,
            "raw": round(raw_prob, 4),
            "calibrated": round(calibrated_prob, 4),
            **(meta or {}),
        }
        with open(self._path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_outcome(self, question_id: str, outcome: int) -> None:
        record = {"type": "outcome", "question_id": question_id, "outcome": outcome}
        with open(self._path, "a") as f:
            f.write(json.dumps(record) + "\n")


class HedgeWeights:
    """Online weight learning via Multiplicative Weights Update (Hedge algorithm).

    Maintains per-agent weights that automatically adapt based on forecasting
    performance. Agents with lower cumulative Brier score get higher weight.
    Minimax optimal — converges to the best agent in hindsight.
    Regret bound: O(sqrt(T * ln(N))).

    Reference: Freund & Schapire's Hedge algorithm.
    """

    def __init__(self, agent_ids: list[str], path: str | None = None) -> None:
        self._agents = agent_ids
        self._cumulative_loss: dict[str, float] = {a: 0.0 for a in agent_ids}
        self._total_updates: int = 0
        self._path = Path(path) if path else None
        if self._path and self._path.exists():
            self._load()

    @property
    def weights(self) -> dict[str, float]:
        """Current normalized weights (sum to 1.0)."""
        n = len(self._agents)
        if n == 0:
            return {}
        # Learning rate: sqrt(ln(N) / T) where T = actual update count
        eta = math.sqrt(math.log(n) / max(1, self._total_updates))
        # Subtract min loss before exp to prevent underflow on large losses
        min_loss = min(self._cumulative_loss.values()) if self._cumulative_loss else 0.0
        raw = {a: math.exp(-eta * (self._cumulative_loss[a] - min_loss))
               for a in self._agents}
        total = sum(raw.values())
        if total == 0:
            return {a: 1.0 / n for a in self._agents}
        return {a: v / total for a, v in raw.items()}

    def update(self, agent_id: str, probability: float, outcome: int) -> None:
        """Update agent's cumulative loss after outcome is known.

        Args:
            agent_id: The agent whose weight to update (must be in agent_ids).
            probability: The agent's predicted probability (0-1).
            outcome: The actual outcome (0 or 1).
        """
        if agent_id not in self._cumulative_loss:
            return  # ignore unknown agents silently
        brier = (probability - outcome) ** 2
        self._cumulative_loss[agent_id] += brier
        self._total_updates += 1
        if self._path:
            self._save()

    def weighted_mean(self, forecasts: dict[str, float]) -> float:
        """Compute weighted mean using current Hedge weights."""
        w = self.weights
        total_weight = 0.0
        weighted_sum = 0.0
        for agent_id, prob in forecasts.items():
            if agent_id in w:
                weighted_sum += w[agent_id] * prob
                total_weight += w[agent_id]
        if total_weight == 0:
            return sum(forecasts.values()) / len(forecasts) if forecasts else 0.5
        return weighted_sum / total_weight

    def _save(self) -> None:
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"losses": self._cumulative_loss, "total_updates": self._total_updates}
            with open(self._path, "w") as f:
                json.dump(data, f)

    def _load(self) -> None:
        if self._path and self._path.exists():
            try:
                with open(self._path) as f:
                    data = json.load(f)
                if isinstance(data, dict) and "losses" in data:
                    self._cumulative_loss = {a: data["losses"].get(a, 0.0) for a in self._agents}
                    self._total_updates = data.get("total_updates", 0)
                elif isinstance(data, dict):
                    # Legacy format: plain {agent: loss} dict
                    self._cumulative_loss = {a: data.get(a, 0.0) for a in self._agents}
            except (json.JSONDecodeError, KeyError):
                pass
