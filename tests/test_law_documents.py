import pytest

from core.law_documents import (
    save_document, list_documents, get_document, get_document_text,
    delete_document, DocumentTooLarge, UnsupportedFileType, MAX_FILE_BYTES,
)


def test_save_and_list_a_plain_text_document():
    record = save_document("torts-notes.txt", b"Negligence requires a duty of care.", category="torts")

    assert record["filename"] == "torts-notes.txt"
    assert record["category"] == "torts"
    assert record["size_bytes"] == len(b"Negligence requires a duty of care.")
    assert record["extraction_error"] is None

    docs = list_documents()
    assert len(docs) == 1
    assert docs[0]["id"] == record["id"]


def test_get_document_text_returns_extracted_content():
    record = save_document("notes.md", b"# Contract law\n\nOffer and acceptance.")
    text = get_document_text(record["id"])
    assert "Offer and acceptance" in text


def test_get_document_text_respects_max_chars():
    record = save_document("notes.txt", b"a" * 1000)
    text = get_document_text(record["id"], max_chars=10)
    assert len(text) == 10


def test_rejects_a_file_over_the_size_limit():
    with pytest.raises(DocumentTooLarge):
        save_document("huge.txt", b"x" * (MAX_FILE_BYTES + 1))


def test_rejects_an_unsupported_file_type():
    with pytest.raises(UnsupportedFileType):
        save_document("virus.exe", b"not a real document")


def test_delete_document_removes_it_from_the_index():
    record = save_document("notes.txt", b"some notes")
    assert get_document(record["id"]) is not None

    existed = delete_document(record["id"])
    assert existed is True
    assert get_document(record["id"]) is None
    assert get_document_text(record["id"]) is None


def test_delete_nonexistent_document_returns_false():
    assert delete_document("does-not-exist") is False


def test_multiple_documents_are_all_listed():
    save_document("a.txt", b"first")
    save_document("b.txt", b"second")
    save_document("c.md", b"# third")

    docs = list_documents()
    assert len(docs) == 3
    assert {d["filename"] for d in docs} == {"a.txt", "b.txt", "c.md"}
