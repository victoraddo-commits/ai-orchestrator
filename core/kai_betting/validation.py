"""KAI Bet — walk-forward validation (§18), calibration (§19), drift (§47).

Pure, deterministic maths (stdlib only). No I/O, no external intelligence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple


# ── Walk-forward (§18) ────────────────────────────────────────────────────────

def walk_forward_splits(n: int, train: int = 200, test: int = 50,
                        step: int | None = None) -> List[Tuple[List[int], List[int]]]:
    """Chronological expanding-origin splits. Prevents future leakage (§55.25)."""
    if n <= 0 or train <= 0 or test <= 0:
        raise ValueError("n, train, test must be positive")
    step = step or test
    out: List[Tuple[List[int], List[int]]] = []
    i = 0
    while i + train + test <= n:
        out.append((list(range(i, i + train)), list(range(i + train, i + train + test))))
        i += step
    return out


# ── Calibration (§19) ─────────────────────────────────────────────────────────

def brier_score(probs: Sequence[float], outcomes: Sequence[float]) -> float:
    _check(probs, outcomes)
    return sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / len(probs)


def log_loss(probs: Sequence[float], outcomes: Sequence[float], eps: float = 1e-15) -> float:
    _check(probs, outcomes)
    total = 0.0
    for p, o in zip(probs, outcomes):
        p = min(max(p, eps), 1 - eps)
        total += o * math.log(p) + (1 - o) * math.log(1 - p)
    return -total / len(probs)


def calibration_bins(probs: Sequence[float], outcomes: Sequence[float],
                     bins: int = 10) -> List[Dict[str, float]]:
    _check(probs, outcomes)
    buckets = [{"bin": b, "n": 0, "sum_p": 0.0, "sum_o": 0.0} for b in range(bins)]
    for p, o in zip(probs, outcomes):
        idx = min(int(p * bins), bins - 1)
        buckets[idx]["n"] += 1
        buckets[idx]["sum_p"] += p
        buckets[idx]["sum_o"] += o
    out = []
    for b in buckets:
        n = b["n"]
        out.append({
            "bin": b["bin"],
            "n": n,
            "avg_predicted": round(b["sum_p"] / n, 6) if n else None,
            "avg_actual": round(b["sum_o"] / n, 6) if n else None,
        })
    return out


def expected_calibration_error(probs: Sequence[float], outcomes: Sequence[float],
                               bins: int = 10) -> float:
    bins_data = calibration_bins(probs, outcomes, bins)
    total = len(probs)
    if not total:
        return 0.0
    ece = 0.0
    for b in bins_data:
        if b["n"]:
            ece += (b["n"] / total) * abs(b["avg_predicted"] - b["avg_actual"])
    return round(ece, 6)


def _check(probs, outcomes):
    if len(probs) != len(outcomes):
        raise ValueError("probs and outcomes must be the same length")
    if not probs:
        raise ValueError("empty input")
    if any(not (0.0 <= p <= 1.0) for p in probs):
        raise ValueError("probabilities must be within [0,1]")
    if any(o not in (0.0, 1.0, 0, 1, True, False) for o in outcomes):
        raise ValueError("outcomes must be binary (0/1)")


# ── Drift (§47) ───────────────────────────────────────────────────────────────

@dataclass
class DriftResult:
    mean_shift: float
    std_ratio: float
    drifted: bool


def drift_score(baseline: Sequence[float], recent: Sequence[float],
                mean_threshold: float = 0.05, std_threshold: float = 0.5) -> DriftResult:
    """Detect distribution shift (probabilities or odds) between windows.

    Flags drift when the mean moves by >= mean_threshold or the std changes by
    a factor >= 1 + std_threshold.
    """
    if not baseline or not recent:
        raise ValueError("baseline and recent must be non-empty")
    eps = 1e-9
    bm = sum(baseline) / len(baseline)
    rm = sum(recent) / len(recent)
    bs = math.sqrt(sum((x - bm) ** 2 for x in baseline) / len(baseline))
    rs = math.sqrt(sum((x - rm) ** 2 for x in recent) / len(recent))
    mean_shift = abs(rm - bm)
    if bs <= eps and rs <= eps:
        std_ratio = 1.0          # both windows flat -> no dispersion change
    elif bs <= eps:
        std_ratio = float("inf")
    else:
        std_ratio = rs / bs
    drifted = mean_shift >= mean_threshold or abs(std_ratio - 1.0) >= std_threshold
    return DriftResult(round(mean_shift, 6), round(std_ratio, 6), drifted)


def drift_psi(baseline: Sequence[float], recent: Sequence[float], bins: int = 10) -> float:
    """Population Stability Index between two distributions (0..inf; >0.25 = drift)."""
    if not baseline or not recent:
        raise ValueError("baseline and recent must be non-empty")
    lo = min(min(baseline), min(recent))
    hi = max(max(baseline), max(recent))
    if hi <= lo:
        return 0.0
    width = (hi - lo) / bins

    def hist(vals):
        h = [0] * bins
        for v in vals:
            idx = min(int((v - lo) / width), bins - 1)
            h[idx] += 1
        return h

    hb, hr = hist(baseline), hist(recent)
    nb, nr = len(baseline), len(recent)
    psi = 0.0
    for i in range(bins):
        pb = hb[i] / nb or 1e-6
        pr = hr[i] / nr or 1e-6
        psi += (pr - pb) * math.log(pr / pb)
    return round(psi, 6)
