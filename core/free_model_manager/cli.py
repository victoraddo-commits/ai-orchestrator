#!/usr/bin/env python3
"""CLI for Free Model Manager.

Usage:
    python -m core.free_model_manager.cli [command]

Commands:
    serve        Start the API server
    discover     Run model discovery
    benchmark    Run benchmark on a model
    status       Show pool status
    health       Run health checks
    test         Test notifications
    init-db      Initialize the database
"""

import argparse
import sys
import os
import logging

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.free_model_manager import (
    FREE_CODING_PORT, LOG_PATH, OPENROUTER_API_KEY,
    OMNIROUTE_BASE_URL
)
from core.free_model_manager.models import db
from core.free_model_manager.discovery import discover_models, test_omniroute_endpoint
from core.free_model_manager.validator import run_full_validation, quick_health_check
from core.free_model_manager.scorer import score_model, get_pool_ranking
from core.free_model_manager.router import (
    get_pool_status, get_current_primary, automatic_failover,
    get_available_models, update_kai_config
)
from core.free_model_manager.notifier import test_telegram_connection
from core.free_model_manager.scheduler import scheduler


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("free_model_manager")


def cmd_serve(args):
    """Start the API server."""
    from core.free_model_manager.api import run_api_server

    print(f"Starting Free Model Manager API on port {FREE_CODING_PORT}")
    print(f"OmniRoute: {OMNIROUTE_BASE_URL}")
    print(f"OpenRouter API: configured" if OPENROUTER_API_KEY else "WARNING: No OpenRouter API key!")

    # Start scheduler
    scheduler.start()

    try:
        run_api_server(args.port)
    except KeyboardInterrupt:
        print("\nShutting down...")
        scheduler.stop()


def cmd_discover(args):
    """Run model discovery."""
    print("Running model discovery...")

    if not test_omniroute_endpoint():
        print("ERROR: OmniRoute not reachable!")
        sys.exit(1)

    try:
        results = scheduler.run_discovery_cycle()

        print(f"\nDiscovery Results:")
        print(f"  Discovered: {results['discovered']}")
        print(f"  Verified Free: {results['verified_free']}")
        print(f"  Coding Qualified: {results['coding_qualified']}")
        print(f"  Duration: {results['duration_seconds']:.1f}s")

        if results.get('errors'):
            print(f"  Errors: {results['errors']}")

        # Show pool status
        pool = get_pool_ranking()
        print(f"\nCurrent Pool:")
        for rank, model in pool.items():
            print(f"  {rank}: {model['model_id']} (coding={model['coding_score']}, overall={model['overall_score']})")

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def cmd_benchmark(args):
    """Run benchmark on a model."""
    model_id = args.model_id

    print(f"Running benchmark on {model_id}...")

    try:
        # Check model exists
        model = db.get_model(model_id)
        if not model:
            print(f"ERROR: Model {model_id} not found in database")
            sys.exit(1)

        # Run full validation
        results = run_full_validation(model_id)

        print(f"\nBenchmark Results for {model_id}:")
        print(f"  Passed: {results['passed_tests']}/{results['total_tests']}")
        print(f"  Overall: {'PASS' if results['overall_pass'] else 'FAIL'}")

        for test_name, result in results['tests'].items():
            status = "✓" if result['passed'] else "✗"
            print(f"    {status} {test_name}: {result['message']}")

        # Score the model
        scores = score_model(model_id, results)

        print(f"\nScores:")
        print(f"  Coding Score: {scores['coding_score']}/10")
        print(f"  Overall Score: {scores['overall_score']}/10")
        print(f"  Qualifies for Pool: {scores['qualifies_for_pool']}")

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def cmd_status(args):
    """Show pool status."""
    print("Free Model Manager Status\n")

    # Health check
    omniroute_ok = test_omniroute_endpoint()
    print(f"OmniRoute: {'✓ OK' if omniroute_ok else '✗ UNREACHABLE'}")
    print(f"OpenRouter API: {'✓ Configured' if OPENROUTER_API_KEY else '✗ NOT CONFIGURED'}")
    print(f"Scheduler: {'✓ Running' if scheduler.is_running() else '✗ Stopped'}")

    # Stats
    stats = db.get_stats()
    print(f"\nDatabase Stats:")
    print(f"  Total Models: {stats['total_models']}")
    print(f"  Verified Free: {stats['verified_free']}")
    print(f"  Active: {stats['active']}")
    print(f"  By Status: {stats['by_status']}")

    # Pool status
    pool_status = get_pool_status()
    primary = get_current_primary()

    print(f"\nPool Status:")
    print(f"  Available Models: {pool_status['available_count']}")
    print(f"  Degraded Models: {pool_status['degraded_count']}")

    if primary:
        print(f"\nPrimary Model:")
        print(f"  Model ID: {primary['model_id']}")
        print(f"  Coding Score: {primary.get('coding_score', 'N/A')}/10")
        print(f"  Overall Score: {primary.get('overall_score', 'N/A')}/10")
        print(f"  Success Rate: {primary.get('success_rate', 'N/A')}%")
        print(f"  P95 Latency: {primary.get('p95_latency', 'N/A')}ms")
    else:
        print("\nPrimary Model: NONE")

    # Pool ranking
    pool = get_pool_ranking()
    print(f"\nModel Pool:")
    for rank, model in pool.items():
        print(f"  {rank}: {model['model_id']} (coding={model['coding_score']}, overall={model['overall_score']})")


def cmd_health(args):
    """Run health checks."""
    print("Running health checks...")

    try:
        results = scheduler.run_health_check_cycle()

        print(f"\nHealth Check Results:")
        print(f"  Models Checked: {results['models_checked']}")
        print(f"  Healthy: {results['healthy']}")
        print(f"  Unhealthy: {results['unhealthy']}")

        if results['failed']:
            print(f"\n  Failed Models:")
            for failure in results['failed']:
                print(f"    - {failure['model']}: {failure['error']}")

        if results['recovered']:
            print(f"\n  Recovered Models:")
            for model_id in results['recovered']:
                print(f"    - {model_id}")

        print(f"  Duration: {results['duration_seconds']:.1f}s")

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def cmd_test(args):
    """Test notifications and connectivity."""
    print("Testing Free Model Manager\n")

    # Test OmniRoute
    print("1. OmniRoute Endpoint:")
    omniroute_ok = test_omniroute_endpoint()
    print(f"   {'✓ OK' if omniroute_ok else '✗ FAILED'}")

    # Test OpenRouter
    print("\n2. OpenRouter API:")
    if OPENROUTER_API_KEY:
        print(f"   ✓ API Key: configured ({OPENROUTER_API_KEY[:8]}...)")
    else:
        print(f"   ✗ API Key: NOT CONFIGURED")

    # Test Telegram
    print("\n3. Telegram Notifications:")
    ok, msg = test_telegram_connection()
    print(f"   {'✓' if ok else '✗'} {msg}")

    # Test inference
    print("\n4. Inference Test:")
    from core.free_model_manager.validator import run_inference
    try:
        success, response, latency = run_inference(
            "openai/gpt-4o-mini",
            "Say 'test' in one word.",
            timeout=30
        )
        if success:
            print(f"   ✓ Inference successful (latency: {latency:.0f}ms)")
            print(f"   Response: {response.strip()[:50]}")
        else:
            print(f"   ✗ Inference failed: {response}")
    except Exception as e:
        print(f"   ✗ Error: {e}")

    print("\nTest complete.")


def cmd_init_db(args):
    """Initialize the database."""
    print("Initializing database...")

    # The database is created automatically when imported
    # Just verify it works
    stats = db.get_stats()
    print(f"Database initialized. Total models: {stats['total_models']}")


def cmd_failover(args):
    """Trigger manual failover."""
    print("Triggering manual failover...")

    try:
        result = automatic_failover()
        print(f"Failover result: {result}")
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Kai Free Model Manager CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start API server")
    serve_parser.add_argument("--port", type=int, default=FREE_CODING_PORT, help="Port to listen on")

    # discover
    subparsers.add_parser("discover", help="Run model discovery")

    # benchmark
    benchmark_parser = subparsers.add_parser("benchmark", help="Run benchmark on model")
    benchmark_parser.add_argument("model_id", help="Model ID to benchmark")

    # status
    subparsers.add_parser("status", help="Show pool status")

    # health
    subparsers.add_parser("health", help="Run health checks")

    # test
    subparsers.add_parser("test", help="Test notifications and connectivity")

    # init-db
    subparsers.add_parser("init-db", help="Initialize database")

    # failover
    subparsers.add_parser("failover", help="Trigger manual failover")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Route to command
    commands = {
        "serve": cmd_serve,
        "discover": cmd_discover,
        "benchmark": cmd_benchmark,
        "status": cmd_status,
        "health": cmd_health,
        "test": cmd_test,
        "init-db": cmd_init_db,
        "failover": cmd_failover,
    }

    command_fn = commands.get(args.command)
    if command_fn:
        command_fn(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
