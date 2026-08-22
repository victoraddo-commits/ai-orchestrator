# Kai Betting Frontend

React 18 + Vite + Tailwind dashboard for the Kai Betting sports prediction platform.

## Quick Start

```bash
npm install
npm run dev     # Dev server on :8095, proxies /api/betting → :8000
npm run build   # Production build → dist/
```

## Pages

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/` | Stats, latest picks, active odds groups |
| Predictions | `/predictions` | Filterable prediction list with sport/status filters |
| Odds Groups | `/odds` | Accumulator groups by risk level |
| Results | `/results` | Settled predictions with win/loss breakdown |
| Performance | `/performance` | Charts, ROI, sport-by-sport analytics |
| Subscribe | `/subscribe` | Payment plans via Hubtel mobile money |
| Account | `/account` | Profile, subscription status, notifications |
| Admin | `/admin` | User management, config, audit logs |

## Tech Stack

- React 18 + React Router 6
- Tailwind CSS 3 (dark theme, custom design system)
- Recharts 2 (line/pie/bar charts)
- Lucide React icons
- Vite 5 (build tool)
- Vitest (testing)
