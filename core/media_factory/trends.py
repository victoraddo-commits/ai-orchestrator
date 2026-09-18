"""Trend discovery + validation (§40/§8).

Discovery fetches a *real* source reachable from CT111 (Google Trends daily
RSS by default). Validation is rule-based and derives every number from the
fetched payload — no traffic, momentum or volume is ever invented. If the
source is unreachable the stage reports DEGRADED and continues.
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from core.media_factory import config, db
from core.media_factory.models import TrendScore, TrendSignal

logger = logging.getLogger(__name__)

HT_NS = "https://trends.google.com/trending/rss"
_USER_AGENT = "KAI-MediaFactory/1.0 (+orchestrator; CT111)"


# ── Pure parsing / scoring (unit-tested without DB or network) ─────────────
def parse_traffic(raw: Optional[str]) -> Optional[int]:
    """Parse Google's human traffic strings: '200+', '2,000+', '10000+'."""
    if not raw:
        return None
    cleaned = raw.strip().replace(",", "").replace("+", "").replace(" ", "")
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def parse_rss(
    xml_text: str,
    *,
    source: str = "google_trends",
    geo: Optional[str] = None,
) -> list[TrendSignal]:
    """Normalize a Google Trends RSS document into TrendSignal rows."""
    root = ET.fromstring(xml_text)
    signals: list[TrendSignal] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        traffic = parse_traffic(
            item.findtext(f"{{{HT_NS}}}approx_traffic")
            or item.findtext("approx_traffic")
        )
        published = item.findtext("pubDate")
        news_items = item.findall(f"{{{HT_NS}}}news_item")
        picture = item.findtext(f"{{{HT_NS}}}picture")
        signals.append(
            TrendSignal(
                source=source,
                title=title,
                external_id=f"{source}:{(geo or '').lower()}:{title.lower()}",
                geo=geo,
                traffic=traffic,
                published=published,
                news_count=len(news_items),
                raw={
                    "approx_traffic": item.findtext(f"{{{HT_NS}}}approx_traffic"),
                    "pubDate": published,
                    "picture": picture,
                    "news_count": len(news_items),
                },
            )
        )
    return signals


def validate(signals: list[TrendSignal]) -> list[dict]:
    """Score each signal against the batch. All values derived from the batch.

    Formulas (documented, rule-based):
      momentum     = traffic / max(traffic)
      competition  = news_count / max(news_count)
      shelf_life   = 1 - competition      (a saturated story has less runway)
      score        = 0.5*momentum + 0.5*shelf_life
    """
    if not signals:
        return []
    max_traffic = max((s.traffic or 0) for s in signals)
    max_news = max(s.news_count for s in signals)
    formula = (
        "momentum=traffic/max_traffic; competition=news/max_news; "
        "shelf_life=1-competition; score=0.5*momentum+0.5*shelf_life"
    )
    scored: list[dict] = []
    for signal in signals:
        momentum = (signal.traffic or 0) / max_traffic if max_traffic else 0.0
        competition = signal.news_count / max_news if max_news else 0.0
        shelf_life = 1.0 - competition
        score = 0.5 * momentum + 0.5 * shelf_life
        scored.append(
            {
                "signal": signal,
                "score": TrendScore(
                    momentum=momentum,
                    competition=competition,
                    shelf_life=shelf_life,
                    score=score,
                    formula=formula,
                ),
                "status": config.STATUS_VERIFIED
                if (max_traffic or max_news)
                else config.STATUS_UNVERIFIED,
            }
        )
    return scored


# ── Fetch ──────────────────────────────────────────────────────────────────
def fetch_google_trends_rss(
    *,
    url: Optional[str] = None,
    geo: Optional[str] = None,
    timeout: Optional[float] = None,
) -> tuple[bool, str]:
    """Fetch the raw RSS document. Returns (ok, xml_or_error_message)."""
    url = url or config.GOOGLE_TRENDS_RSS
    timeout = timeout or config.TREND_HTTP_TIMEOUT
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return False, f"HTTP {response.status}"
            return True, response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


# ── Persistence ────────────────────────────────────────────────────────────
def store_scored(scored: list[dict], *, geo: Optional[str]) -> list[int]:
    ids: list[int] = []
    for entry in scored:
        signal: TrendSignal = entry["signal"]
        score: TrendScore = entry["score"]
        try:
            row = db.insert_returning(
                """
                INSERT INTO trends
                    (source, external_id, title, query, geo, raw, normalized,
                     momentum, competition, shelf_life, score, status, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (source, title, geo) DO UPDATE SET
                    raw = EXCLUDED.raw,
                    normalized = EXCLUDED.normalized,
                    momentum = EXCLUDED.momentum,
                    competition = EXCLUDED.competition,
                    shelf_life = EXCLUDED.shelf_life,
                    score = EXCLUDED.score,
                    status = EXCLUDED.status,
                    fetched_at = now(),
                    updated_at = now()
                RETURNING id
                """,
                (
                    signal.source,
                    signal.external_id,
                    signal.title,
                    signal.title,
                    geo,
                    db.jsonb(signal.raw),
                    db.jsonb(score.to_dict()),
                    score.momentum,
                    score.competition,
                    score.shelf_life,
                    score.score,
                    entry["status"],
                ),
            )
            if row:
                ids.append(row["id"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("trend store failed for %r (%s)", signal.title, type(exc).__name__)
    return ids


def discover(
    *,
    geo: Optional[str] = None,
    source_url: Optional[str] = None,
    persist: bool = True,
) -> dict:
    """Fetch → normalize → validate → persist. Always returns a status."""
    geo = geo or config.TREND_GEO
    ok, payload = fetch_google_trends_rss(url=source_url, geo=geo)
    if not ok:
        result = {
            "status": config.STATUS_DEGRADED,
            "source": source_url or config.GOOGLE_TRENDS_RSS,
            "geo": geo,
            "error": payload,
            "fetched": 0,
            "scored": 0,
            "stored": 0,
        }
        db.audit("trend.discover.degraded", payload={"geo": geo, "error": payload})
        return result
    signals = parse_rss(payload, geo=geo)
    scored = validate(signals)
    stored = store_scored(scored, geo=geo) if persist else []
    result = {
        "status": config.STATUS_VERIFIED if scored else config.STATUS_UNVERIFIED,
        "source": source_url or config.GOOGLE_TRENDS_RSS,
        "geo": geo,
        "error": None,
        "fetched": len(signals),
        "scored": len(scored),
        "stored": len(stored),
    }
    db.record_event("trend_discovery", result["status"], detail=result)
    db.audit("trend.discover", payload=result)
    return result


def latest(limit: int = 50, offset: int = 0) -> list[dict]:
    return db.query(
        """
        SELECT * FROM trends
        ORDER BY score DESC NULLS LAST, fetched_at DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )


def validated(limit: int = 50) -> list[dict]:
    return db.query(
        """
        SELECT * FROM trends
        WHERE status = 'VERIFIED' AND score IS NOT NULL
        ORDER BY score DESC NULLS LAST
        LIMIT %s
        """,
        (limit,),
    )
