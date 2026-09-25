# Horse racing in Kai Bet — status and integration path

**Status: approved scope, `no_source`.** Horse racing is a first-class sport in
the registry (`core/kai_betting/scope.py`), the schema (`SPORT_SEEDS` /
`MARKET_SEEDS` in `core/kai_betting/db.py`) and the provider registry
(`core/kai_betting/data_sources/horse_racing.py`), but **no configured odds
provider publishes racing odds**. Nothing is fabricated: the adapter reports
`no_source` and returns no events/odds.

Check the live status:

```bash
curl -sk -H "Authorization: Bearer $(cat /root/.ai-orchestrator/api_token)" \
  https://127.0.0.1:8000/api/betting/sources
```

The response contains `data.sports["horse_racing"] == "no_source"` and
`data.providers.horse_racing.status == "no_source"`.

Why no source: the three wired providers are
`OddsAPIioSource` (odds-api.io, 34 sports — **no racing**),
`SportsGameOddsSource` (a fixed 8-league catalog, no racing) and the legacy
`OddsAPISource` (soccer/hockey/basketball only). Verified against the live
odds-api.io `/v3/sports` catalog on 2026-09-25.

## Plugging in a racing feed later

Candidate feeds (one):
- The Racing API — https://www.theracingapi.com
- Timeform / Racing Post data services
- Betfair Exchange API (Betting / Historical Stream)
- Sportradar Racing

Steps:

1. **Implement the adapter** in `core/kai_betting/data_sources/horse_racing.py`
   and replace the placeholder methods:
   - `fetch_events()` must return Odds-API.io-shaped dicts so
     `DataIngestionManager._ingest_event_v3` consumes them unchanged:
     `{id, home, away, date, status, league: {name, slug}, sport: {name, slug}}`.
     For racing, `home` is the meeting/race label and `away` the race time or
     distance marker.
   - `fetch_odds(event_id, ...)` returns the normalised market rows used by
     `OddsAPIioSource.extract_markets` (`market_type`, `selection`, `odds`,
     `line`, `bookmaker`, …).
   - Set `is_configured` from the feed key and make `provider_status` return
     `{"connected": True, "status": "connected", ...}` once live.
2. **Scope/slug wiring**: add the feed's meeting slugs to
   `scope._HORSE_RACING` (or its name keywords) and add a
   `"horse-racing": "horse_racing"` entry to
   `data_sources/odds_api_io.SPORT_SLUG_MAP` only if that provider actually
   serves it. Remove `"horse_racing"` from the `SPORTS_WITH_SOURCE` exclusion
   in `scope.py` and add `"horse_racing"` to `DEFAULT_SYNC_SPORTS`.
3. **Provider registry**: `HorseRacingSource` is already in
   `data_sources.DATA_SOURCES`; `source_status()` will flip the sport from
   `no_source` to `available` automatically.
4. **Market mapping**: win maps to `match_result`; add `place`/`each_way`
   normalisation in `odds_api_io.MARKET_NORMALIZATION` and mark the racing
   families `modelled=True` in `core/kai_betting/markets.py`.
5. **Tests**: extend `tests/kai_betting/test_multi_sport_scope.py` (adapter
   contract) and `test_league_priority.py` (meeting whitelist).
