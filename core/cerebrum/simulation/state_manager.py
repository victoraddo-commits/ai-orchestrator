"""Simulation state management — checkpoint and restore world states during simulation runs."""
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import copy


class SimulationStateManager:
    def __init__(self):
        self._checkpoints: List[Dict[str, Any]] = []
        self._current_state: Optional[Dict[str, Any]] = None

    def capture(self, state: Dict[str, Any]) -> str:
        checkpoint = {
            "id": f"ckpt-{len(self._checkpoints):04d}",
            "state": copy.deepcopy(state),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._checkpoints.append(checkpoint)
        self._current_state = copy.deepcopy(state)
        return checkpoint["id"]

    def restore(self, checkpoint_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if checkpoint_id is None:
            return copy.deepcopy(self._current_state) if self._current_state else None

        for ckpt in self._checkpoints:
            if ckpt["id"] == checkpoint_id:
                return copy.deepcopy(ckpt["state"])
        return None

    def list_checkpoints(self) -> List[Dict[str, Any]]:
        return [{"id": c["id"], "timestamp": c["timestamp"]} for c in self._checkpoints]

    def clear(self):
        self._checkpoints.clear()
        self._current_state = None
