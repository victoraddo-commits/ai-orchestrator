"""test-gen CLI entry point.

Commands:
  generate  — Generate test files for given source files.
  verify    — Run the test runner against generated tests.
  smoke     — Run an end-to-end generation + verification fixture.
"""

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="test-gen",
        description="Verify coding generation with opencode_claude front",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen_parser = sub.add_parser("generate", help="Generate test files for source files")
    gen_parser.add_argument(
        "files", nargs="+", help="Python source files or directories to generate tests for"
    )
    gen_parser.add_argument(
        "--provider", default="opencode_claude",
        help="Coding provider to use (default: opencode_claude)"
    )
    gen_parser.add_argument(
        "--output", "-o", default=None,
        help="Output directory for generated tests (default: tests/generated)"
    )
    gen_parser.add_argument(
        "--timeout", type=int, default=None,
        help="Timeout in seconds for the generation call"
    )

    verify_parser = sub.add_parser("verify", help="Run generated tests")
    verify_parser.add_argument(
        "--test-dir", default=None,
        help="Directory containing generated tests (default: tests/generated)"
    )
    verify_parser.add_argument(
        "--runner", default=None,
        help="Test runner command (default: pytest)"
    )

    smoke_parser = sub.add_parser("smoke", help="Run end-to-end smoke test")
    smoke_parser.add_argument(
        "--provider", default="opencode_claude",
        help="Coding provider to use (default: opencode_claude)"
    )
    smoke_parser.add_argument(
        "--timeout", type=int, default=None,
        help="Timeout in seconds"
    )
    smoke_parser.add_argument(
        "--keep-output", action="store_true",
        help="Keep generated smoke test files after run"
    )

    args = parser.parse_args(argv)

    if args.command == "generate":
        cmd_generate(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "smoke":
        cmd_smoke(args)


def cmd_generate(args):
    from core.test_gen.discovery import resolve_sources
    from core.test_gen.generator import generate_tests
    from core.test_gen.writer import ensure_test_dir, discover_generated_tests

    sources = resolve_sources(args.files)
    if not sources:
        print("No Python source files found.", file=sys.stderr)
        sys.exit(1)

    test_dir = Path(args.output) if args.output else Path("tests/generated")
    test_dir = test_dir.resolve()
    ensure_test_dir(test_dir)

    print(f"Generating tests for {len(sources)} file(s) using {args.provider}...")
    result = generate_tests(
        source_files=sources,
        test_dir=test_dir,
        provider_name=args.provider,
        timeout=args.timeout,
    )

    if result.get("success"):
        generated = discover_generated_tests(test_dir)
        print(f"Success. {len(generated)} test file(s) written to {test_dir}")
        for f in generated:
            print(f"  {f}")
    else:
        errors = result.get("tool_errors", [])
        for e in errors:
            print(f"Error: {e.get('content', str(e))}", file=sys.stderr)
        sys.exit(1)

    if result.get("cost") is not None:
        print(f"Cost: ${result['cost']:.6f}")


def cmd_verify(args):
    from core.test_gen.verifier import run_tests
    from core.test_gen.config import DEFAULT_GENERATED_TEST_DIR

    test_dir = Path(args.test_dir) if args.test_dir else Path(DEFAULT_GENERATED_TEST_DIR)

    print(f"Running tests in {test_dir}...")
    result = run_tests(test_dir, runner=args.runner)

    if result["stdout"]:
        print(result["stdout"])
    if result["stderr"]:
        print(result["stderr"], file=sys.stderr)

    if result["passed"]:
        print(f"All tests passed ({len(result['test_files_checked'])} file(s)).")
    else:
        print(f"Tests FAILED (returncode={result['returncode']}).", file=sys.stderr)
        sys.exit(1)


def cmd_smoke(args):
    from core.test_gen.smoke import run_smoke

    print(f"Running smoke test with provider '{args.provider}'...")
    result = run_smoke(
        provider_name=args.provider,
        timeout=args.timeout,
        keep_output=args.keep_output,
    )

    if result["passed"]:
        print("Smoke test PASSED.")
    else:
        print("Smoke test FAILED.", file=sys.stderr)
        gen = result.get("generation_result", {})
        if gen:
            errors = gen.get("tool_errors", [])
            for e in errors:
                print(f"  Generation error: {e.get('content', str(e))}", file=sys.stderr)
        ver = result.get("verification_result", {})
        if ver:
            print(f"  Verification: {ver.get('stderr', '')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
