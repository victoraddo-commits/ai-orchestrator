"""Tests for SecondBrainRouter."""
from __future__ import annotations

import tempfile
import os
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from core.second_brain.router import SecondBrainRouter, ROUTER_STORE_NAMES
from core.second_brain.types import (
    Confidence,
    MemoryType,
    MergePolicy,
    QueryResult,
    SecondBrainRecord,
    SourceAuthority,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_record(
    entity: str,
    memory_type: MemoryType = MemoryType.OPERATIONAL,
    confidence: Confidence = Confidence.CONFIRMED,
    source_authority: SourceAuthority = SourceAuthority.SECOND_BRAIN,
    timestamp: str | None = None,
    id: str = "rec-1",
) -> SecondBrainRecord:
    return SecondBrainRecord(
        id=id,
        entity=entity,
        memory_type=memory_type,
        confidence=confidence,
        source_authority=source_authority,
        timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
        fact={"text": f"fact for {entity}"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStoresForTypes:
    """test_query_decomposes_to_stores — verify correct stores are queried for given memory_types."""

    def test_all_memory_types_queries_all_stores(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        stores = router._stores_for_types(None)
        assert set(stores) == set(ROUTER_STORE_NAMES)

    def test_all_memory_types_wildcard_queries_all_stores(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        stores = router._stores_for_types(["*"])
        assert set(stores) == set(ROUTER_STORE_NAMES)

    def test_infrastructure_maps_to_operational(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        stores = router._stores_for_types([MemoryType.INFRASTRUCTURE])
        assert stores == ["operational"]

    def test_semantic_maps_to_cognitive(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        stores = router._stores_for_types([MemoryType.SEMANTIC])
        assert stores == ["cognitive"]

    def test_conversation_maps_to_conversational(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        stores = router._stores_for_types([MemoryType.CONVERSATION])
        assert stores == ["conversational"]

    def test_project_maps_to_project(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        stores = router._stores_for_types([MemoryType.PROJECT])
        assert stores == ["project"]

    def test_relationship_maps_to_relationship(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        stores = router._stores_for_types([MemoryType.RELATIONSHIP])
        assert stores == ["relationship"]

    def test_multiple_memory_types_deduplicates_stores(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        stores = router._stores_for_types([
            MemoryType.INFRASTRUCTURE,
            MemoryType.INCIDENT,
            MemoryType.OPERATIONAL,
        ])
        # All three map to "operational" — should appear once
        assert stores == ["operational"]

    def test_mixed_memory_types_multiple_stores(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        stores = router._stores_for_types([
            MemoryType.INFRASTRUCTURE,   # operational
            MemoryType.SEMANTIC,          # cognitive
            MemoryType.PROJECT,           # project
        ])
        assert set(stores) == {"operational", "cognitive", "project"}

    def test_legal_supplemental_skipped(self):
        router = SecondBrainRouter(stores_base="/tmp/fake")
        # DOCUMENT maps to relationship; PROCEDURAL maps to project
        # No MemoryType maps to legal_supplemental in MEMORY_TYPE_STORE,
        # so this is a sanity check that the store is never returned
        stores = router._stores_for_types([MemoryType.RELATIONSHIP])
        assert "legal_supplemental" not in stores


class TestQueryTimeRangeConversion:
    """test_query_time_range_converted — verify dict time_range is converted to tuple for stores."""

    def test_dict_time_range_converted_to_tuple(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SecondBrainRouter(stores_base=tmpdir)
            # Pre-create a minimal store dir so lazy init doesn't fail
            os.makedirs(f"{tmpdir}/operational", exist_ok=True)

            mock_store = MagicMock()
            mock_store.scan.return_value = []
            router._stores["operational"] = mock_store

            router.query({
                "entity": None,
                "memory_types": [MemoryType.INFRASTRUCTURE],
                "time_range": {"start": "2024-01-01T00:00:00Z", "end": "2024-12-31T23:59:59Z"},
                "limit": 50,
            })

            mock_store.scan.assert_called_once()
            call_kwargs = mock_store.scan.call_args.kwargs
            assert call_kwargs["time_range"] == (
                "2024-01-01T00:00:00Z",
                "2024-12-31T23:59:59Z",
            )

    def test_none_time_range_passed_as_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SecondBrainRouter(stores_base=tmpdir)
            os.makedirs(f"{tmpdir}/operational", exist_ok=True)

            mock_store = MagicMock()
            mock_store.scan.return_value = []
            router._stores["operational"] = mock_store

            router.query({
                "entity": None,
                "memory_types": [MemoryType.INFRASTRUCTURE],
                "time_range": None,
                "limit": 50,
            })

            mock_store.scan.assert_called_once()
            assert mock_store.scan.call_args.kwargs["time_range"] is None


class TestQueryLimit:
    """test_query_respects_limit — verify limit is passed through."""

    def test_limit_passed_to_store_scan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SecondBrainRouter(stores_base=tmpdir)
            os.makedirs(f"{tmpdir}/operational", exist_ok=True)

            mock_store = MagicMock()
            mock_store.scan.return_value = []
            router._stores["operational"] = mock_store

            router.query({
                "memory_types": [MemoryType.INFRASTRUCTURE],
                "limit": 25,
            })

            mock_store.scan.assert_called_once()
            assert mock_store.scan.call_args.kwargs["limit"] == 25


class TestMergeNewestWins:
    """test_merge_newest_wins — two records for same entity, newest wins."""

    def test_newest_timestamp_wins(self):
        older = make_record(
            "svc-a", timestamp="2024-01-01T00:00:00Z", id="rec-old"
        )
        newer = make_record(
            "svc-a", timestamp="2024-06-01T00:00:00Z", id="rec-new"
        )

        router = SecondBrainRouter(stores_base="/tmp/fake")
        results = [
            QueryResult(records=[older], store="operational", merge_policy=MergePolicy.NEWEST_WINS),
            QueryResult(records=[newer], store="operational", merge_policy=MergePolicy.NEWEST_WINS),
        ]

        merged = router._merge(results, merge_policy_override=None)

        assert len(merged) == 1
        assert merged[0].id == "rec-new"

    def test_different_entities_both_kept(self):
        rec_a = make_record("entity-a", timestamp="2024-01-01T00:00:00Z", id="rec-a")
        rec_b = make_record("entity-b", timestamp="2024-01-01T00:00:00Z", id="rec-b")

        router = SecondBrainRouter(stores_base="/tmp/fake")
        results = [
            QueryResult(records=[rec_a], store="operational", merge_policy=MergePolicy.NEWEST_WINS),
            QueryResult(records=[rec_b], store="operational", merge_policy=MergePolicy.NEWEST_WINS),
        ]

        merged = router._merge(results, merge_policy_override=None)

        assert len(merged) == 2


class TestMergeSourceAuthority:
    """test_merge_source_authority — two records for same entity, highest authority wins."""

    def test_lowest_tier_number_wins(self):
        low_authority = make_record(
            "svc-b",
            source_authority=SourceAuthority.HISTORICAL_CHAT,  # tier 6
            id="rec-low",
        )
        high_authority = make_record(
            "svc-b",
            source_authority=SourceAuthority.LIVE_SYSTEM,  # tier 1
            id="rec-high",
        )

        router = SecondBrainRouter(stores_base="/tmp/fake")
        results = [
            QueryResult(records=[low_authority], store="cognitive", merge_policy=MergePolicy.SOURCE_AUTHORITY),
            QueryResult(records=[high_authority], store="cognitive", merge_policy=MergePolicy.SOURCE_AUTHORITY),
        ]

        merged = router._merge(results, merge_policy_override=None)

        assert len(merged) == 1
        assert merged[0].id == "rec-high"

    def test_override_policy_applies_to_all(self):
        older_high_authority = make_record(
            "svc-c",
            timestamp="2024-01-01T00:00:00Z",
            source_authority=SourceAuthority.LIVE_SYSTEM,
            id="rec-old",
        )
        newer_low_authority = make_record(
            "svc-c",
            timestamp="2024-06-01T00:00:00Z",
            source_authority=SourceAuthority.INFERENCE,  # tier 7
            id="rec-new",
        )

        router = SecondBrainRouter(stores_base="/tmp/fake")
        results = [
            QueryResult(records=[older_high_authority], store="operational", merge_policy=MergePolicy.NEWEST_WINS),
            QueryResult(records=[newer_low_authority], store="operational", merge_policy=MergePolicy.NEWEST_WINS),
        ]

        # Override with SOURCE_AUTHORITY — high authority should win even though older
        merged = router._merge(results, merge_policy_override=MergePolicy.SOURCE_AUTHORITY)

        assert len(merged) == 1
        assert merged[0].id == "rec-old"


class TestMergeUnionAll:
    """UNION_ALL keeps all records, deduplicates by id."""

    def test_union_all_keeps_all_records_different_entities(self):
        rec1 = make_record("ent-1", id="id-1")
        rec2 = make_record("ent-2", id="id-2")

        router = SecondBrainRouter(stores_base="/tmp/fake")
        results = [
            QueryResult(records=[rec1], store="relationship", merge_policy=MergePolicy.UNION_ALL),
            QueryResult(records=[rec2], store="relationship", merge_policy=MergePolicy.UNION_ALL),
        ]

        merged = router._merge(results, merge_policy_override=None)

        assert len(merged) == 2

    def test_union_all_deduplicates_by_id(self):
        rec1 = make_record("ent-1", id="shared-id")

        router = SecondBrainRouter(stores_base="/tmp/fake")
        results = [
            QueryResult(records=[rec1], store="relationship", merge_policy=MergePolicy.UNION_ALL),
            QueryResult(records=[rec1], store="relationship", merge_policy=MergePolicy.UNION_ALL),
        ]

        merged = router._merge(results, merge_policy_override=None)

        assert len(merged) == 1


class TestRequireConfirmation:
    """test_require_confirmation_flags_inference + no_flag_for_confirmed."""

    def test_inferred_record_gets_flag(self):
        inferred = make_record(
            "svc-x",
            confidence=Confidence.INFERRED,
            id="rec-inferred",
        )

        router = SecondBrainRouter(stores_base="/tmp/fake")
        flagged = router._apply_confirmation_flag([inferred])

        assert len(flagged) == 1
        assert "UNCONFIRMED_INFERENCE" in flagged[0].metadata.get("_flags", [])

    def test_confirmed_record_not_flagged(self):
        confirmed = make_record(
            "svc-y",
            confidence=Confidence.CONFIRMED,
            id="rec-confirmed",
        )

        router = SecondBrainRouter(stores_base="/tmp/fake")
        flagged = router._apply_confirmation_flag([confirmed])

        assert len(flagged) == 1
        assert "UNCONFIRMED_INFERENCE" not in flagged[0].metadata.get("_flags", [])

    def test_documented_record_not_flagged(self):
        documented = make_record(
            "svc-z",
            confidence=Confidence.DOCUMENTED,
            id="rec-documented",
        )

        router = SecondBrainRouter(stores_base="/tmp/fake")
        flagged = router._apply_confirmation_flag([documented])

        assert len(flagged) == 1
        assert "UNCONFIRMED_INFERENCE" not in flagged[0].metadata.get("_flags", [])

    def test_require_confirmation_in_query_dict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SecondBrainRouter(stores_base=tmpdir)
            os.makedirs(f"{tmpdir}/operational", exist_ok=True)

            inferred = make_record(
                "svc-w",
                confidence=Confidence.INFERRED,
                id="rec-inferred",
            )
            mock_store = MagicMock()
            mock_store.scan.return_value = [inferred]
            router._stores["operational"] = mock_store

            results = router.query({
                "memory_types": [MemoryType.OPERATIONAL],
                "require_confirmation": True,
                "limit": 100,
            })

            assert len(results) == 1
            assert "UNCONFIRMED_INFERENCE" in results[0].metadata.get("_flags", [])


class TestEmptyStoresList:
    """Guard against max_workers=0 when no stores match."""

    def test_empty_stores_list_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SecondBrainRouter(stores_base=tmpdir)
            # Query with memory types that don't map to any store (nonexistent type)
            from core.second_brain.types import MemoryType

            result = router.query({
                "memory_types": ["__nonexistent__"],  # type: ignore
                "limit": 100,
            })
            assert result == []


class TestNullEntityMerge:
    """Null-entity records must not collapse into a single result."""

    def test_null_entity_records_not_collapsed(self):
        null_a = make_record(None, id="null-a", timestamp="2024-01-01T00:00:00Z")
        null_b = make_record(None, id="null-b", timestamp="2024-01-02T00:00:00Z")
        null_c = make_record(None, id="null-c", timestamp="2024-01-03T00:00:00Z")

        router = SecondBrainRouter(stores_base="/tmp/fake")
        results = [
            QueryResult(records=[null_a], store="operational", merge_policy=MergePolicy.NEWEST_WINS),
            QueryResult(records=[null_b], store="cognitive", merge_policy=MergePolicy.NEWEST_WINS),
            QueryResult(records=[null_c], store="conversational", merge_policy=MergePolicy.NEWEST_WINS),
        ]

        merged = router._merge(results, merge_policy_override=None)

        # All 3 null-entity records must be present (not collapsed to 1)
        assert len(merged) == 3
        ids = {r.id for r in merged}
        assert ids == {"null-a", "null-b", "null-c"}


class TestParallelFanOut:
    """test_parallel_fan_out — verify ThreadPoolExecutor is used."""

    def test_thread_pool_executor_used(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SecondBrainRouter(stores_base=tmpdir)
            for store_name in ROUTER_STORE_NAMES:
                os.makedirs(f"{tmpdir}/{store_name}", exist_ok=True)

            # Patch AppendOnlyStore to track instantiation
            from unittest.mock import MagicMock
            mock_instances = {}
            original_init = router._get_store

            def tracking_get_store(name):
                if name not in mock_instances:
                    m = MagicMock()
                    m.scan.return_value = []
                    mock_instances[name] = m
                return mock_instances[name]

            router._get_store = tracking_get_store

            # Patch ThreadPoolExecutor to verify it's used
            with patch("core.second_brain.router.ThreadPoolExecutor") as mock_tpe:
                mock_tpe.return_value.__enter__ = MagicMock()
                mock_tpe.return_value.__exit__ = MagicMock()

                from concurrent.futures import Future
                future_store_names = {}

                def fake_submit(fn, *args, **kwargs):
                    fut = Future()
                    # Capture the store name from the submitted call
                    store_name = args[0]
                    future_store_names[fut] = store_name
                    # Return immediately resolved future with empty results
                    fut.set_result(QueryResult(records=[], store=store_name, merge_policy=MergePolicy.NEWEST_WINS))
                    return fut

                mock_tpe.return_value.__enter__.return_value.submit = fake_submit
                mock_tpe.return_value.__enter__.return_value.as_completed = MagicMock(
                    return_value=[]
                )

                router.query({"memory_types": None, "limit": 50})

                mock_tpe.assert_called_once()
                # Verify max_workers was set to number of stores
                call_kwargs = mock_tpe.call_args.kwargs
                assert call_kwargs.get("max_workers") == len(ROUTER_STORE_NAMES)


class TestStoreExceptionHandling:
    """test_store_exception_doesnt_crash — one store fails, others still return results."""

    def test_store_exception_doesnt_crash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            router = SecondBrainRouter(stores_base=tmpdir)
            os.makedirs(f"{tmpdir}/operational", exist_ok=True)
            os.makedirs(f"{tmpdir}/cognitive", exist_ok=True)

            # Successful store
            good_store = MagicMock()
            good_record = make_record("good-entity", id="good-rec")
            good_store.scan.return_value = [good_record]

            # Failing store
            bad_store = MagicMock()
            bad_store.scan.side_effect = RuntimeError("store read error")

            router._stores["operational"] = good_store
            router._stores["cognitive"] = bad_store

            # Query both stores
            results = router.query({
                "memory_types": [MemoryType.INFRASTRUCTURE, MemoryType.SEMANTIC],
                "limit": 100,
            })

            # Good store's result should be in the output
            assert len(results) >= 1
            assert any(r.id == "good-rec" for r in results)
