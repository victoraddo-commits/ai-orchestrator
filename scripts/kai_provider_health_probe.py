"""Periodic health probe for providers marked quota_exceeded.

2026-08-02 operator directive: a periodic script that proactively finds
every provider currently marked quota_exceeded, sends each a minimal real
test call, and calls provider_health.clear_quota_exceeded(name) immediately
on success -- so real task routing benefits right away instead of waiting
out the expiry. Also notifies via Telegram when a provider comes back
online, so the operator knows without checking manually.

Usage: .venv/bin/python scripts/kai_provider_health_probe.py

Based on scripts/kai_qwen3_digest.py's structure/imports/sys.path setup.
"""
import sys
from pathlib import Path

# Setup the path to import modules from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.ai.provider_health as provider_health
import core.ai_provider as ai_provider
import core.telegram_bridge as telegram_bridge


def main():
    # Find all providers currently marked as quota_exceeded
    all_snapshots = provider_health.get_all_quota_snapshots()
    
    # Filter to providers that are currently quota_exceeded
    quota_exceeded_providers = [
        name for name, snapshot in all_snapshots.items()
        if snapshot and snapshot.get("status") == "quota_exceeded"
    ]
    
    if not quota_exceeded_providers:
        # No providers to probe, just print a status line
        print("No providers currently marked as quota_exceeded")
        return
    
    # Track providers that recovered successfully
    recovered_providers = []
    
    for provider_name in quota_exceeded_providers:
        try:
            # Look up the provider in the registry
            provider = ai_provider.get_provider(provider_name)
            
            # Skip if provider doesn't exist or is not available
            if provider is None or (provider.get("available_fn") and not provider["available_fn"]()):
                continue
            
            # Determine the appropriate probing method based on provider capabilities
            if provider.get("run_text_task") is not None:
                # Text task providers - minimal, fast probe
                try:
                    result = provider["run_text_task"]("Reply with exactly the word: pong", timeout=20)
                    if result and "pong" in result.lower():
                        provider_health.clear_quota_exceeded(provider_name)
                        recovered_providers.append(provider_name)
                except Exception:
                    # Failed probe is expected/normal - not an error in the script
                    pass
                    
            elif provider.get("run_coding_task") is not None:
                # Coding task providers - more expensive real call
                try:
                    # Create a scratch workspace for the probe
                    import tempfile
                    import os
                    from core.repo_manager import create_local_repo
                    
                    # Create a temporary directory for the probe
                    temp_dir = tempfile.mkdtemp()
                    try:
                        # Create a local repo for the probe
                        create_local_repo(temp_dir)
                        
                        # Minimal instruction for the probe
                        instruction = "Create a file named health_check.txt containing exactly the word: ok. Do nothing else."
                        
                        # Run the probe with a longer timeout since it's a real coding task
                        result = provider["run_coding_task"](temp_dir, instruction, timeout=90)
                        
                        # Check if the probe was successful
                        if result.get("success") is True:
                            provider_health.clear_quota_exceeded(provider_name)
                            recovered_providers.append(provider_name)
                    finally:
                        # Clean up the temporary directory
                        import shutil
                        shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    # Failed probe is expected/normal - not an error in the script
                    pass
        except Exception:
            # Skip any providers that cause issues during the probing process
            continue
    
    # Send Telegram notification if any providers recovered
    if recovered_providers:
        provider_count = len(recovered_providers)
        provider_list = ", ".join(recovered_providers)
        message = f"\U0001f7e2 Kai: {provider_count} provider(s) back online: {provider_list}"
        try:
            telegram_bridge.send_message(message)
            print(message)
        except Exception as e:
            # If Telegram fails, just print to stdout
            print(f"Failed to send Telegram notification: {e}")
            print(message)
    else:
        # Print a status line when no providers recovered
        print("Health probe completed. No providers recovered.")


if __name__ == "__main__":
    main()