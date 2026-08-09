# Kai Health Observatory — Permanent Health Worker Design

**Date**: 2026-08-09
**Part of**: Kai Mobile Command Node — Sub-project 4
**Status**: Approved → Implementing

## Overview

A permanent background health worker thread within the scheduler process that
continuously samples system health independently of the orchestrator cycle,
detects anomalies with adaptive baselines, forecasts resource exhaustion,
and feeds the mobile Command Center with trend data.

## Why

The existing orchestrator cycle runs health checks every 60s but:
- Health checks pause during long-running build generations (up to 15 min)
- No historical trending — each cycle is point-in-time
- No baseline-aware anomaly detection — only fixed thresholds
- No disk/memory growth forecasting
- No health history for the mobile Command Center to visualize

## Architecture

```
HealthWorker (daemon thread, 30s loop)
├── Sampling: scanner.scan() → system_state snapshot
├── Storage: SQLite (WAL mode) in memory/health_observatory.db
├── Anomaly Detection: z-score > 3 vs adaptive baseline
├── Trend Detection: linear regression on sliding windows
├── Forecasting: projected time-to-full for disk/memory
└── Output: NotificationManager.enqueue() + health_observatory API
```

## Database Schema

```
health_snapshots
  id INTEGER PK, timestamp TEXT, data JSON, node TEXT
  
health_metrics
  id INTEGER PK, timestamp TEXT, metric TEXT, value REAL, node TEXT
  
health_anomalies  
  id INTEGER PK, timestamp TEXT, metric TEXT, node TEXT,
  baseline REAL, actual REAL, z_score REAL, severity TEXT,
  acked INTEGER DEFAULT 0
  
health_baselines
  metric TEXT PK, node TEXT, mean REAL, stddev REAL,
  sample_count INTEGER, last_updated TEXT
```

## Key Behaviors

1. **Independent sampling** — every 30s, never blocked by build processing
2. **Adaptive baselines** — learned from 24h+ of data, slowly adapting (EMA)
3. **Anomaly detection** — z-score > 3 triggers notification
4. **Trend detection** — linear regression on 1h/6h/24h windows
5. **Forecasting** — projected exhaustion time for disk and memory
6. **Multi-resolution** — 30s samples (24h), 5min rollups (7d), hourly (30d)
7. **Feeds notification system** — anomalies → NotificationManager
8. **API endpoints** — trends, anomalies, health score for Command Center

## Files

| File | Purpose |
|------|---------|
| `core/health_worker.py` | HealthWorker thread class |
| `core/health_observatory.py` | SQLite storage, anomaly detection, forecasting |
| `core/health_worker_routes.py` | FastAPI router for health trends/anomalies |
| `tests/test_health_worker.py` | Tests |

## Integration

- `core/scheduler.py` — start/stop HealthWorker alongside DeepSeekPool and TelegramMonitor
- `core/api.py` — include_router for health worker routes
- `core/notifications.py` — anomaly enqueue destination (already built)
