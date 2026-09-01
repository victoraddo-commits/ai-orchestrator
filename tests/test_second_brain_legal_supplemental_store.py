"""Tests for the legal_supplemental passthrough store."""
import pytest

from core.second_brain.stores.legal_supplemental import LegalSupplementalStore, store
from core.second_brain.types import MergePolicy


class TestLegalSupplementalStore:
    def test_legal_store_returns_empty(self):
        """scan() returns empty list, get_current() returns None."""
        s = LegalSupplementalStore()
        assert s.scan() == []
        assert s.scan(entity="any-entity") == []
        assert s.get_current("any-entity") is None

    def test_legal_store_has_correct_merge_policy(self):
        """MERGE_POLICY is SOURCE_AUTHORITY."""
        s = LegalSupplementalStore()
        assert s.MERGE_POLICY == MergePolicy.SOURCE_AUTHORITY

    def test_store_instance_has_correct_merge_policy(self):
        """The exported store singleton also has SOURCE_AUTHORITY."""
        assert store.MERGE_POLICY == MergePolicy.SOURCE_AUTHORITY

    def test_store_name(self):
        """STORE_NAME is legal_supplemental."""
        s = LegalSupplementalStore()
        assert s.STORE_NAME == "legal_supplemental"

    def test_history_returns_empty(self):
        """history() returns empty list for any entity."""
        s = LegalSupplementalStore()
        assert s.history("any-entity") == []
