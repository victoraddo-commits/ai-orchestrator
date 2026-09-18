"""Team control commands (roadmap 21P, wired to the live runtime in Phase 1).

Parses the operator team commands (/teams, /team, /create-team, /assign,
/mission, /pause-team, /resume-team, /replace, /why-failed) and dispatches them
through an injected handler so the Command Center / Mission Engine owns the
logic.

When no handler is injected (the Telegram/control-command path), the default
handler is built on the live :class:`core.teammate.engine.WorkforceEngine`, so
the commands work at runtime instead of replying "no team dispatcher
configured".
"""
from __future__ import annotations

COMMANDS = ("/teams", "/team", "/create-team", "/assign", "/mission",
            "/pause-team", "/resume-team", "/replace", "/why-failed",
            "/module", "/module-request")


def parse_team_command(text: str):
    parts = (text or "").strip().split()
    if not parts:
        return (None, [])
    cmd = parts[0].lower()
    if cmd not in COMMANDS:
        return (None, [])
    return (cmd, parts[1:])


def handle_team_command(text: str, dispatcher=None) -> str:
    cmd, args = parse_team_command(text)
    if cmd is None:
        return ""
    if dispatcher is None:
        dispatcher = default_dispatcher
    result = dispatcher(cmd, args)
    return result if isinstance(result, str) else str(result)


def default_dispatcher(cmd: str, args: list) -> str:
    """Runtime team commands, backed by the WorkforceEngine."""
    from core.teammate.engine import WorkforceEngine

    engine = WorkforceEngine()

    if cmd == "/teams":
        teams = engine.list_teams()
        if not teams:
            return "No teams yet. Try: /create-team coder or /mission <goal>"
        lines = [f"*TEAMS* ({len(teams)})"]
        for t in teams[:10]:
            lines.append(f"- `{t['id']}` {t['status']} "
                         f"[{', '.join(t.get('specializations') or [])}] "
                         f"members={len(t.get('member_ids') or [])}")
        return "\n".join(lines)

    if cmd == "/team":
        if not args:
            return "usage: /team <team_id>"
        team = engine.get_team(args[0])
        if team is None:
            return f"team {args[0]} not found"
        lines = [f"team `{team['id']}` status={team['status']}",
                 f"requirement: {team['requirement'][:120]}"]
        for m in team.get("members") or []:
            lines.append(f"- {m.get('specialization')} `{m.get('id')}` "
                         f"status={m.get('status')} skills={len(m.get('skills') or [])}")
        return "\n".join(lines)

    if cmd == "/create-team":
        if not args:
            return "usage: /create-team <role> [skill ...]"
        role = args[0]
        skills = args[1:] or None
        try:
            mate = engine.create_teammate(role, skills=skills)
        except Exception as exc:  # noqa: BLE001
            return f"create-team failed: {type(exc).__name__}: {exc}"
        return (f"teammate `{mate['id']}` role={mate['specialization']} "
                f"status={mate['status']} skills={', '.join(mate.get('skills') or []) or '-'}")

    if cmd in ("/mission", "/assign"):
        # /mission <goal...>            → auto-form a team + execute
        # /assign <team_id> <goal...>   → execute on an existing team
        if not args:
            return f"usage: {cmd} <goal>"
        team_id = None
        goal_args = args
        if cmd == "/assign":
            team_id, goal_args = args[0], args[1:]
        goal = " ".join(goal_args).strip()
        if not goal:
            return f"usage: {cmd} <goal>"
        try:
            mission = engine.create_mission(goal, execute=True, background=True,
                                            team_id=team_id)
        except Exception as exc:  # noqa: BLE001
            return f"mission start failed: {type(exc).__name__}: {exc}"
        return (f"mission `{mission['id']}` started "
                f"(team `{mission['team_id']}`, {len(mission['tasks'])} tasks) — "
                f"check /team or GET /api/missions/{mission['id']}")

    if cmd == "/module":
        from core.integration.module_bridge import get_bridge
        bridge = get_bridge()
        if not args:
            rows = bridge.list_module_capabilities()
            lines = [f"*MODULES* ({len(rows)})"]
            for r in rows[:20]:
                caps = ", ".join((r.get("capabilities") or [])[:3]) or "-"
                lines.append(f"- `{r['module']}` -> {r['specialization']} [{caps}]")
            return "\n".join(lines)
        row = bridge.list_module_capabilities(args[0])
        return (f"module `{row['module']}` specialization={row['specialization']} "
                f"skills={', '.join(row['skills'])} "
                f"capabilities={', '.join(row['capabilities'])}")

    if cmd == "/module-request":
        if len(args) < 2:
            return "usage: /module-request <module> <capability> [objective...]"
        from core.integration.module_bridge import get_bridge
        module, capability = args[0], args[1]
        objective = " ".join(args[2:]).strip() or None
        try:
            result = get_bridge().request_capability(
                module, capability, objective=objective, execute=True,
                background=True)
        except Exception as exc:  # noqa: BLE001
            return f"module request failed: {type(exc).__name__}: {exc}"
        return (f"module `{result['module']}` capability `{result['capability']}` — "
                f"teammate `{result['teammate_id']}` "
                f"mission `{result['mission_id']}` started")

    if cmd == "/pause-team":
        return "pause-team: acknowledged (mission steering owns pause/resume)"
    if cmd == "/resume-team":
        return "resume-team: acknowledged (mission steering owns pause/resume)"
    if cmd == "/replace":
        return "replace: acknowledged — use /create-team to materialize a replacement"
    if cmd == "/why-failed":
        if not args:
            return "usage: /why-failed <mission_id>"
        mission = engine.get_mission(args[0])
        if mission is None:
            return f"mission {args[0]} not found"
        failed = [t for t in mission["tasks"] if t["status"] != "COMPLETED"]
        if not failed:
            return f"mission {args[0]} has no failed tasks (status={mission['status']})"
        return "\n".join(f"- {t['skill_id']}: {t['error'] or t['status']}"
                         for t in failed)
    return f"{cmd}: unknown"
