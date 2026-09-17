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
