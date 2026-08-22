# Kai Betting

AI sports prediction worker for the Kai platform. Runs a 300-second cycle that ingests
sports data (Odds-API.io primary, The Odds API legacy), generates predictions and odds
groups, and pushes daily picks to Telegram subscribers.

## Runtime

Single process entry point:

```bash
python -m core.kai_betting.run_worker
```

### Environment

| Var | Purpose |
|-----|---------|
| `KAI_BETTING_DB` | SQLite database path (default `memory/kai_betting.db`) |
| `ODDS_API_IO_KEY` | Odds-API.io v3 API key (primary data source) |
| `ODDS_API_KEY` | The Odds API key — legacy events fallback, and currently the **sole source of final scores/settlement**: `_sync_results_legacy()` is the only code path that writes finished-game scores, and it no-ops entirely if this is unset |
| `ODDS_API_PROVIDER` | Odds data provider override |
| `SPORTSGAMEODDS_API_KEY` | SportsGameOdds API key (supplemental odds+results source; free tier covers MLB/MLS/NBA/NCAAB/NCAAF/NFL/NHL/UEFA Champions League only) |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token for daily pick notifications |
| `BETTING_ADMIN_CHAT_ID` | Telegram chat ID that receives the zero-picks alert (fires when games had odds today but none cleared quality filters) |
| `GPUAI_API_KEY` | GPU.ai serverless API key for the AI inference layer (never committed, never exposed to the frontend) |
| `GPUAI_BASE_URL` | GPU.ai API base (default `https://api.gpu.ai/v1`) |
| `GPUAI_DAILY_LIMIT` / `GPUAI_WEEKLY_LIMIT` / `GPUAI_MONTHLY_LIMIT` | AI inference budget ceilings (USD; daily default `3.0`, 0 = unlimited) |

The AI layer (`core/kai_betting/ai/`) routes three on-demand models through one
client — Qwen (`gpuai/qwen3.7-plus`), DeepSeek (`gpuai/deepseek-v4-pro`), Kimi K3
(`gpuai/kimi-k3`) — escalating only candidates that justify deeper analysis. If
`GPUAI_API_KEY` is unset the layer is a no-op and Kai falls back to the local
statistical engine.

### Docker

```bash
docker build -t kai-betting .
docker run --rm -e KAI_BETTING_DB=/data/kai_betting.db -e ODDS_API_IO_KEY=... \
  -e TELEGRAM_BOT_TOKEN=... -v "$PWD/data:/data" kai-betting
```

## Layout

- `core/kai_betting/` — the application package (worker, engines, data sources, DB).
- `core/kai_betting/frontend/` — React/Vite dashboard (dev-only, not part of the worker image).

<!-- autodeploy verification -->

<!-- autodeploy verification -->
