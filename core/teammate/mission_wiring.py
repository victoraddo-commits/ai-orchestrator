"""Mission ↔ teammate wiring (roadmap 21E).

Wires the teammate Factory into missions: given a mission requirement, the
Factory assembles the team and the linker records which teammates serve which
mission so ``/kai/missions/{id}/team`` can report it.
"""
from __future__ import annotations


class MissionTeamLinker:
    def __init__(self, factory=None):
        self.factory = factory
        self._teams: dict = {}

    def assign_team(self, mission_id: str, requirement, plan=None) -> list:
        if self.factory is None:
            raise ValueError("a factory is required to assign a team")
        members = self.factory.assemble_engineering_team(requirement, plan=plan)
        self._teams[mission_id] = [getattr(m, "id", None) for m in members]
        return members

    def team_for(self, mission_id: str) -> list:
        return list(self._teams.get(mission_id, []))

    def team_payload(self, mission_id: str) -> dict:
        members = self.team_for(mission_id)
        return {"mission_id": mission_id, "teammate_ids": members,
                "size": len(members)}
