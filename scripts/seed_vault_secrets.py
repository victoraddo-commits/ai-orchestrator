"""Seed all live .env secrets into the credential vault.

This script migrates all active API keys, tokens, and passwords from
ai-orchestrator/.env into the credential vault (AES-256-GCM + kai-vault).

Run once after any vault deployment or credential rotation.

Usage:
    python scripts/seed_vault_secrets.py

Secrets are read directly from .env via dotenv_values() — no shell export needed,
and no secrets appear in the script body or git history.
"""
import os
import sys
from pathlib import Path

# Load .env directly so we don't need shell expansion
from dotenv import dotenv_values

sys.path.insert(0, '/project/ai-orchestrator')

from core.ai.credential_vault import store_credential

# Map provider slug -> (env_var_name, api_base)
# Only includes LIVE secrets — dead entries (not read by any code) are excluded.
PROVIDERS = [
    ("gemini",              "GEMINI_API_KEY",              "https://generativelanguage.googleapis.com"),
    ("groq",                "GROQ_API_KEY",                "https://api.groq.com"),
    ("openai",              "OPENAI_API_KEY",              "https://api.openai.com"),
    ("openrouter",          "OPENROUTER_API_KEY",          "https://openrouter.ai"),
    ("minimax",             "MINIMAX_API_KEY",             ""),
    ("deepseek_native_pro", "DEEPSEEK_NATIVE_PRO_API_KEY",  "https://api.deepseek.com"),
    ("deepseek_native_flash","DEEPSEEK_NATIVE_FLASH_API_KEY","https://api.deepseek.com"),
    ("deepseek_openrouter", "DEEPSEEK_OPENROUTER_API_KEY",  "https://openrouter.ai"),
    ("odds_api_io",         "ODDS_API_IO_KEY",            "https://api.odds-api.io"),
    ("kai_telegram",        "KAI_TELEGRAM_BOT_TOKEN",     ""),
    ("juris_kai",           "JURIS_KAI_BOT_TOKEN",       ""),
    ("proxmox_b",           "PROXMOX_B_TOKEN_ID",         ""),
    ("proxmox_b_secret",    "PROXMOX_B_TOKEN_SECRET",     ""),
    ("opnsense",            "OPNSENSE_API_KEY",           "http://192.168.99.3"),
    ("opnsense_secret",     "OPNSENSE_API_SECRET",        ""),
    ("ddwrt",               "DDWRT_PASSWORD",             ""),
    # Additional secrets identified by audit — may not be in .env yet
    ("anthropic_auth",      "ANTHROPIC_AUTH_TOKEN",       ""),
    ("ha_token",            "HA_TOKEN",                   ""),
    ("hubtel_client_secret","HUBTEL_CLIENT_SECRET",       ""),
    ("hubtel_client_id",    "HUBTEL_CLIENT_ID",          ""),
    ("hubtel_merchant_num", "HUBTEL_MERCHANT_NUMBER",    ""),
    ("geminix",             "GEMINIX_API_KEY",            "https://generativelanguage.googleapis.com"),
    ("gpuai",               "GPUAI_API_KEY",              ""),  # pre-existed
]

# Load .env from the project root
env_path = Path(__file__).parent.parent / ".env"
env = dotenv_values(env_path)

stored = []
failed = []
skipped = []

for provider, env_var, base in PROVIDERS:
    key = env.get(env_var, "")
    if not key:
        skipped.append((provider, env_var))
        print(f"  SKIP (not set): {provider} ({env_var})")
        continue
    try:
        store_credential(provider, key, base)
        stored.append(provider)
        print(f"  Stored: {provider}")
    except Exception as e:
        failed.append((provider, str(e)))
        print(f"  FAILED: {provider} — {e}")

print(f"\nVault seed complete: {len(stored)} stored, {len(skipped)} skipped, {len(failed)} failed")
if failed:
    print("Failed providers:", [f[0] for f in failed])
    sys.exit(1)
elif skipped:
    print("Skipped (not in .env):", [f"{s[0]}({s[1]})" for s in skipped])
