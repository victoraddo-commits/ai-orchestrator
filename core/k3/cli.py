"""K3 CLI — Isolate self-modifying build workspace.

Usage:
    k3 run --workspace ./src --persist discard -- make build
    k3 run --workspace . --persist report -- npm test
    k3 run --workspace . --persist commit -- make build
    k3 run --workspace . --persist artifacts --artifacts "dist/*.tar.gz" -o ./out -- make build
"""

import argparse
import sys

from core.k3.config import K3Config, PersistPolicy, NetworkPolicy
from core.k3 import run_build


def build_parser():
    parser = argparse.ArgumentParser(
        prog="k3",
        description="Isolate self-modifying build workspace using overlayfs",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a build in an isolated workspace")
    run_p.add_argument("--workspace", "-w", default=".", help="Workspace path (default: .)")
    run_p.add_argument("--persist", "-p", default="discard",
                       choices=["discard", "report", "commit", "artifacts"],
                       help="Persistence policy (default: discard)")
    run_p.add_argument("--network", "-n", action="store_true", help="Allow network access")
    run_p.add_argument("--timeout", "-t", type=int, default=300, help="Timeout in seconds (default: 300)")
    run_p.add_argument("--memory", "-m", help="Memory limit (e.g. 512m)")
    run_p.add_argument("--cpus", "-c", help="CPU limit (e.g. 1.0)")
    run_p.add_argument("--artifacts", "-a", nargs="*", default=[], help="Artifact patterns for 'artifacts' policy")
    run_p.add_argument("--output", "-o", help="Output directory for 'artifacts' policy")
    run_p.add_argument("--env", "-e", action="append", default=[], help="Environment variables (KEY=VALUE)")
    run_p.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run")

    sub.add_parser("version", help="Show K3 version")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "version":
        from core.k3 import __version__  # noqa: F811
        print(f"k3 {__version__}")
        return 0

    if args.command == "run":
        return _cmd_run(args)

    return 1


def _cmd_run(args):
    if not args.cmd:
        print("Error: no command specified", file=sys.stderr)
        return 1

    env = {}
    for pair in args.env:
        if "=" in pair:
            k, v = pair.split("=", 1)
            env[k] = v

    try:
        config = K3Config(
            workspace_path=args.workspace,
            command=args.cmd,
            persist=PersistPolicy(args.persist),
            network=NetworkPolicy.HOST if args.network else NetworkPolicy.NONE,
            env=env,
            timeout=args.timeout,
            memory_limit=args.memory,
            cpu_limit=args.cpus,
            artifact_patterns=args.artifacts,
            artifact_output_dir=args.output,
        )
        config.validate()
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        result = run_build(config)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    if not result.succeeded:
        print(f"Build failed with exit code {result.exit_code}", file=sys.stderr)
        if result.timed_out:
            print(f"Build timed out after {config.timeout}s", file=sys.stderr)
        if result.stderr:
            print(result.stderr, file=sys.stderr)

    if result.stdout:
        print(result.stdout)

    if result.changes and result.changes.has_changes():
        print(f"\nChanges: {result.changes.total_changes()} total "
              f"({len(result.changes.created)} created, "
              f"{len(result.changes.modified)} modified, "
              f"{len(result.changes.deleted)} deleted)")

    return result.exit_code if result.exit_code is not None else 1


if __name__ == "__main__":
    sys.exit(main())
