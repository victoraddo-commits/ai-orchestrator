"""KAI Bet — validation API: calibration, walk-forward, drift (§18/§19/§47)."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.kai_betting.validation import (
    walk_forward_splits, brier_score, log_loss, calibration_bins,
    expected_calibration_error, drift_score, drift_psi,
)

validation_router = APIRouter(tags=["Kai Betting — Validation"])


class CalibrationRequest(BaseModel):
    probs: List[float]
    outcomes: List[float]
    bins: int = 10


class WalkForwardRequest(BaseModel):
    n: int
    train: int = 200
    test: int = 50
    step: Optional[int] = None


class DriftRequest(BaseModel):
    baseline: List[float]
    recent: List[float]
    bins: int = 10


@validation_router.post("/validation/calibration")
def api_calibration(body: CalibrationRequest):
    try:
        return {
            "brier": brier_score(body.probs, body.outcomes),
            "log_loss": log_loss(body.probs, body.outcomes),
            "ece": expected_calibration_error(body.probs, body.outcomes, body.bins),
            "bins": calibration_bins(body.probs, body.outcomes, body.bins),
            "n": len(body.probs),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@validation_router.post("/validation/walk-forward")
def api_walk_forward(body: WalkForwardRequest):
    try:
        splits = walk_forward_splits(body.n, body.train, body.test, body.step)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "folds": len(splits),
        "first": {"train": len(splits[0][0]), "test": len(splits[0][1])} if splits else None,
        "last": {"train_range": [min(splits[-1][0]), max(splits[-1][0])],
                 "test_range": [min(splits[-1][1]), max(splits[-1][1])]} if splits else None,
    }


@validation_router.post("/validation/drift")
def api_drift(body: DriftRequest):
    try:
        d = drift_score(body.baseline, body.recent)
        return {"mean_shift": d.mean_shift, "std_ratio": d.std_ratio, "drifted": d.drifted,
                "psi": drift_psi(body.baseline, body.recent, body.bins)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
