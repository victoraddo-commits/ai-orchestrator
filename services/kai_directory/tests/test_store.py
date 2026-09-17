import os
import tempfile

from services.kai_directory.models import Record
from services.kai_directory.store import RecordStore


def make_store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return RecordStore(path), path


def test_upsert_and_get():
    store, _ = make_store()
    store.upsert(Record(id="money", name="money", display_name="Money",
                        category="finances", host="ct108", ip="192.168.1.118", port=8095))
    got = store.get("money")
    assert got is not None and got.display_name == "Money"


def test_reconcile_marks_stale_but_keeps_records():
    store, _ = make_store()
    store.upsert(Record(id="money", name="money", ip="192.168.1.118", port=8095, source="docker"))
    store.upsert(Record(id="bet", name="bet", ip="192.168.1.111", port=8000, source="docker"))
    # next sweep only sees 'money'
    store.reconcile([Record(id="money", name="money", ip="192.168.1.118", port=8095, source="docker")])
    assert store.get("money") is not None
    assert store.get("bet") is not None          # kept, not wiped
    assert store.get("bet").source == "docker:stale"


def test_manual_records_survive_reconcile():
    store, _ = make_store()
    store.upsert(Record(id="manual-1", name="manual-1", source="manual"))
    store.reconcile([])
    assert store.get("manual-1") is not None


def test_list_and_delete():
    store, _ = make_store()
    store.upsert(Record(id="a", name="a", category="infra"))
    store.upsert(Record(id="b", name="b", category="infra"))
    assert len(store.list(category="infra")) == 2
    store.delete("a")
    assert store.get("a") is None


def test_q_filter_searches_name_display_and_tags():
    store, _ = make_store()
    store.upsert(Record(id="a", name="alpha", display_name="Alpha Service", tags="legal"))
    store.upsert(Record(id="b", name="beta", display_name="Beta", tags="betting"))
    assert {r.id for r in store.list(q="Alpha")} == {"a"}      # display_name
    assert {r.id for r in store.list(q="beta")} == {"b"}       # name
    assert {r.id for r in store.list(q="legal")} == {"a"}      # tags


def test_list_ordered_by_category_then_name():
    store, _ = make_store()
    store.upsert(Record(id="z", name="zeta", category="b"))
    store.upsert(Record(id="a", name="alpha", category="b"))
    store.upsert(Record(id="m", name="mu", category="a"))
    assert [r.id for r in store.list()] == ["m", "a", "z"]


def test_reconcile_is_idempotent():
    store, _ = make_store()
    store.upsert(Record(id="money", name="money", source="docker"))
    disc = [Record(id="money", name="money", source="docker")]
    assert store.reconcile(disc)["stale"] == 0
    assert store.reconcile(disc)["stale"] == 0     # second pass doesn't re-mark


def test_manual_record_not_clobbered_by_discovered():
    store, _ = make_store()
    store.upsert(Record(id="money", name="money", category="finance", source="manual"))
    store.reconcile([Record(id="money", name="money", category="auto", source="docker")])
    got = store.get("money")
    assert got.source == "manual"
    assert got.category == "finance"
