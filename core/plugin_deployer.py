"""Rebuild and redeploy the CloudCLI plugin bundle after a self-modifying merge.

Confirmed live 2026-07-30 (17H): 17B merged the chat panel into
ai-orchestrator-plugin/src/index.ts cleanly (correct code, tests passed,
visible in git log), but the bundle CloudCLI actually loads from
~/.claude-code-ui/plugins/ai-orchestrator/dist/ was still dated 2026-07-29.
The self-build pipeline merges TypeScript *source* into the live plugin
repo but had no step that ran `npm run build` and copied the compiled
dist/ output to where CloudCLI serves it from -- every plugin-side change
was silently invisible until a human rebuilt and copied by hand. This is
exactly the class of gap 17C closed for the Python services, so this
module mirrors 17C's changed-file-scoped design (core.service_restarter)
rather than inventing a parallel ad-hoc mechanism.

Rules (per roadmap phase 17H acceptance criteria):
- Only a successful self-modifying merge whose deploy actually merged the
  plugin repo may trigger a rebuild. Failed/rolled-back deploys, container
  deploys, and orchestrator-only merges never rebuild the bundle or
  restart cloudcli.service.
- A plugin-repo merge that touches no bundle source (README, plugin
  tests, ...) triggers no rebuild -- restarting CloudCLI drops its open
  sessions for no reason.
- A failed `npm run build` is caught and logged, and the previously
  deployed bundle is left completely untouched -- never half-deployed.
- If we cannot determine what the plugin merge changed, rebuild anyway:
  a stale bundle is a silent, hard-to-diagnose failure, an extra rebuild
  and CloudCLI restart is a blip.
"""

import os
import shutil
import subprocess
from pathlib import Path

from core.deployment_manager import _changed_files_of_merge
from core.logger import error, info, warning


CLOUDCLI_SERVICE = "cloudcli.service"

# Where CloudCLI actually serves the compiled plugin bundle from. This is
# NOT the live plugin repo's own dist/ -- confirmed live 2026-07-30: the
# repo built fine while this directory kept serving yesterday's bundle.
CLOUDCLI_PLUGIN_DIST = Path.home() / ".claude-code-ui" / "plugins" / "ai-orchestrator" / "dist"

# Paths (relative to the plugin repo root) whose change invalidates the
# compiled bundle. Everything else in that repo (README, tests/, docs)
# never reaches dist/.
_BUNDLE_SOURCE_PREFIXES = ("src/",)
_BUNDLE_SOURCE_FILES = {"package.json", "package-lock.json", "tsconfig.json", "manifest.json"}

# `npm run build` is a plain `tsc` over two source files today; 300s is
# generous headroom even if npm decides to fuss over its cache.
NPM_BUILD_TIMEOUT = 300


def _plugin_repo_path():
    # Lazy import, mirroring deployment_manager: core.roadmap_manager
    # imports build-pipeline modules and importing it at module load from
    # here would risk an import cycle via core.build_manager.
    from core.roadmap_manager import PLUGIN_PROJECT_PATH

    return PLUGIN_PROJECT_PATH


def _affects_bundle(path):
    path = path.replace("\\", "/")
    if path in _BUNDLE_SOURCE_FILES:
        return True
    return any(path.startswith(prefix) for prefix in _BUNDLE_SOURCE_PREFIXES)


def plugin_needs_rebuild(changed_files):
    """Did this plugin-repo merge invalidate the compiled bundle?

    `changed_files=None` means "unknown": rebuild rather than risk serving
    a stale bundle (same fail-safe posture as 17C's restart logic).
    """
    if changed_files is None:
        warning(
            "plugin-redeploy: changed files of the plugin merge are unknown -- "
            "conservatively rebuilding the bundle rather than risk serving stale code"
        )
        return True
    return any(_affects_bundle(f) for f in changed_files)


def _npm_build(repo, timeout=NPM_BUILD_TIMEOUT):
    return subprocess.run(
        ["npm", "run", "build"], cwd=str(repo), capture_output=True, text=True, timeout=timeout
    )


def _systemctl(*args, timeout=60):
    return subprocess.run(
        ["systemctl", *args], capture_output=True, text=True, timeout=timeout
    )


def _copy_bundle(dist_dir, target_dir):
    """Copy the compiled dist/*.js and *.js.map into the served dist dir.

    Each file is staged alongside its destination and swapped in with an
    atomic os.replace, so a crash mid-copy can never leave a truncated
    file where CloudCLI reads it. Raises on any failure -- the caller
    records the error instead of restarting CloudCLI onto a broken bundle.
    """
    artifacts = sorted(
        entry
        for entry in Path(dist_dir).iterdir()
        if entry.is_file() and (entry.name.endswith(".js") or entry.name.endswith(".js.map"))
    )
    if not artifacts:
        raise RuntimeError(f"npm run build produced no .js artifacts in {dist_dir}")

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    for artifact in artifacts:
        staged = target_dir / (artifact.name + ".staging")
        shutil.copy2(artifact, staged)
        os.replace(staged, target_dir / artifact.name)

    return [artifact.name for artifact in artifacts]


def _restart_cloudcli(trigger):
    info(f"plugin-redeploy: restarting {CLOUDCLI_SERVICE} (trigger: {trigger})")

    try:
        result = _systemctl("restart", CLOUDCLI_SERVICE)
    except Exception as err:  # systemctl missing, timeout, ...
        error(f"plugin-redeploy: restart of {CLOUDCLI_SERVICE} raised: {err}")
        return {"restarted": False, "error": str(err)}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        error(f"plugin-redeploy: restart of {CLOUDCLI_SERVICE} failed: {detail}")
        return {"restarted": False, "error": detail}

    info(f"plugin-redeploy: {CLOUDCLI_SERVICE} restart succeeded")
    return {"restarted": True}


def _rebuild_and_deploy(repo, trigger):
    info(f"plugin-redeploy: rebuilding CloudCLI plugin bundle in {repo} (trigger: {trigger})")

    try:
        result = _npm_build(repo)
    except Exception as err:  # npm missing, timeout, ...
        error(
            f"plugin-redeploy: npm run build raised: {err} -- "
            f"previously deployed bundle left untouched"
        )
        return {"rebuilt": False, "deployed": False, "restarted": False, "error": str(err)}

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()[-2000:]
        error(
            f"plugin-redeploy: npm run build failed -- "
            f"previously deployed bundle left untouched: {detail}"
        )
        return {"rebuilt": False, "deployed": False, "restarted": False, "error": detail}

    try:
        copied = _copy_bundle(Path(repo) / "dist", CLOUDCLI_PLUGIN_DIST)
    except Exception as err:
        # Don't restart CloudCLI onto a bundle we couldn't fully deploy --
        # the previous bundle files are still individually intact (each
        # copy is an atomic replace) and keep being served as-is.
        error(f"plugin-redeploy: copying bundle to {CLOUDCLI_PLUGIN_DIST} failed: {err}")
        return {"rebuilt": True, "deployed": False, "restarted": False, "error": str(err)}

    info(f"plugin-redeploy: copied {', '.join(copied)} to {CLOUDCLI_PLUGIN_DIST}")

    restart = _restart_cloudcli(trigger)
    return {"rebuilt": True, "deployed": True, "copied": copied, **restart}


def redeploy_plugin_if_needed(build, deploy_result):
    """Rebuild+redeploy the CloudCLI bundle made stale by a plugin merge.

    Called by build_manager after a successful self-modifying deploy,
    BEFORE the 17C service restarts (the scheduler restart kills the very
    process running this -- the npm build must finish first). No-op for
    failed deploys, container deploys, orchestrator-only merges, and
    plugin merges that touched no bundle source. The outcome is attached
    to the deploy result (deploy_result["plugin_redeploy"]) for
    observability and returned.
    """
    if not deploy_result or not deploy_result.get("deployed"):
        return None
    if not deploy_result.get("merged_branch"):
        # Container deploy -- no plugin repo involved.
        return None

    repo = _plugin_repo_path()
    plugin_commit = (deploy_result.get("merged_repos") or {}).get(str(repo))
    if plugin_commit is None:
        # Orchestrator-only merge: the plugin repo was not a target of
        # this deploy at all -- never rebuild or restart CloudCLI for it.
        return None

    build_name = build.get("name", build.get("id", "?"))
    changed_files = _changed_files_of_merge(repo, plugin_commit)

    if not plugin_needs_rebuild(changed_files):
        info(
            f"plugin-redeploy: deploy of build {build_name} touched no bundle source "
            f"in the plugin repo (changed: {changed_files}) -- no rebuild needed"
        )
        record = {
            "rebuilt": False,
            "deployed": False,
            "restarted": False,
            "reason": "merge touched no bundle source",
        }
        deploy_result["plugin_redeploy"] = record
        return record

    changed_summary = "unknown" if changed_files is None else ", ".join(changed_files[:20])
    trigger = f"self-modifying merge of build {build_name} (plugin changed: {changed_summary})"

    record = _rebuild_and_deploy(repo, trigger)
    deploy_result["plugin_redeploy"] = record
    return record
