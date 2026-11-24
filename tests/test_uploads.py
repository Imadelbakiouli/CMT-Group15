"""Tests for T08: safe PDF uploads (SEC-05, SEC-10, NFR-03). These tests do not need a database."""
import importlib.util
import io
import os
import pathlib

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, TextStringObject
from werkzeug.datastructures import FileStorage

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("cmt_uploads", ROOT / "cmt_service" / "uploads.py")
uploads = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uploads)

SECRET_AUTHOR = "Alice Secret"


@pytest.fixture(autouse=True)
def upload_folder(tmp_path, monkeypatch):
    """Every test stores into its own empty folder."""
    folder = tmp_path / "uploads"
    monkeypatch.setenv("UPLOAD_DIR", str(folder))
    return folder


def make_pdf(author=SECRET_AUTHOR, pages=1, with_javascript=False, encrypt=False):
    """A small PDF. The author's name is in the metadata and in a comment; optionally with JavaScript."""
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Author": author, "/Title": f"Paper by {author}", "/Creator": author})
    note = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Text"),
        NameObject("/Rect"): ArrayObject([FloatObject(0), FloatObject(0), FloatObject(10), FloatObject(10)]),
        NameObject("/T"): TextStringObject(author), NameObject("/Contents"): TextStringObject("note"),
    })
    page[NameObject("/Annots")] = ArrayObject([writer._add_object(note)])
    if with_javascript:
        writer.add_js("app.alert('document level')")
        action = DictionaryObject({NameObject("/S"): NameObject("/JavaScript"),
                                   NameObject("/JS"): TextStringObject("app.alert('page level')")})
        page[NameObject("/AA")] = DictionaryObject({NameObject("/O"): action})
    if encrypt:
        writer.encrypt("password", algorithm="RC4-128")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def refused(data, status):
    with pytest.raises(uploads.UploadError) as caught:
        uploads.store_pdf(io.BytesIO(data))
    assert caught.value.status == status
    return caught.value


# ---- storing (SEC-10) ----

def test_valid_pdf_is_stored_under_a_random_name(upload_folder):
    upload = FileStorage(stream=io.BytesIO(make_pdf()), filename="../../etc/evil.pdf", content_type="text/plain")
    name = uploads.store_pdf(upload)
    assert uploads.STORED_NAME.fullmatch(name)                      # 32 random hex characters + .pdf
    assert "evil" not in name and ".." not in name                  # nothing of the client's file name is used
    stored = upload_folder / name
    assert stored.is_file() and stored.parent == upload_folder.resolve()
    assert stored.read_bytes().startswith(b"%PDF-")
    assert [p.name for p in upload_folder.iterdir()] == [name]      # and nothing else was written


def test_same_file_uploaded_twice_gets_two_different_names():
    data = make_pdf()
    assert uploads.store_pdf(io.BytesIO(data)) != uploads.store_pdf(io.BytesIO(data))


@pytest.mark.skipif(os.name != "posix", reason="file modes work differently on Windows")
def test_stored_file_is_only_accessible_to_the_service(upload_folder):
    name = uploads.store_pdf(io.BytesIO(make_pdf()))
    assert (upload_folder / name).stat().st_mode & 0o077 == 0       # no access for group or others


# ---- what is refused (SEC-10) ----

def test_files_that_are_not_pdfs_are_refused():
    samples = {
        "text": b"hello, I am not a pdf",
        "png": b"\x89PNG\r\n\x1a\n" + b"0" * 100,
        "windows program": b"MZ\x90\x00" + b"0" * 100,
        "html": b"<html><script>alert(1)</script></html>",
        "zip": b"PK\x03\x04" + b"0" * 100,
        "pdf marker not at the start": b"<html>" + make_pdf(),
    }
    for kind, data in samples.items():
        assert refused(data, 415).message == "Only PDF files are accepted", kind


def test_pdf_header_with_garbage_inside_is_refused():
    refused(b"%PDF-1.7\nthis is not really a pdf\n" + b"0" * 200, 422)


def test_truncated_pdf_is_refused():
    refused(make_pdf()[:150], 422)


def test_empty_upload_is_refused():
    refused(b"", 400)


def test_encrypted_pdf_is_refused():
    refused(make_pdf(encrypt=True), 422)


def test_pdf_with_too_many_pages_is_refused(monkeypatch):
    monkeypatch.setattr(uploads, "MAX_PAGES", 2)
    refused(make_pdf(pages=3), 422)
    uploads.store_pdf(io.BytesIO(make_pdf(pages=2)))                 # exactly at the limit is fine


def test_refused_uploads_leave_nothing_behind(upload_folder):
    for data in (b"text", b"%PDF-1.7 garbage", b""):
        with pytest.raises(uploads.UploadError):
            uploads.store_pdf(io.BytesIO(data))
    assert not upload_folder.exists() or list(upload_folder.iterdir()) == []


# ---- size limit (SEC-10, NFR-03) ----

def test_limit_is_20_megabytes():
    assert uploads.MAX_BYTES == 20 * 1024 * 1024


def test_file_over_20_megabytes_is_refused():
    refused(b"%PDF-" + b"0" * uploads.MAX_BYTES, 413)               # one byte too many


def test_size_limit_is_exact(monkeypatch):
    data = make_pdf()
    monkeypatch.setattr(uploads, "MAX_BYTES", len(data))
    uploads.store_pdf(io.BytesIO(data))                              # exactly the limit: accepted
    monkeypatch.setattr(uploads, "MAX_BYTES", len(data) - 1)
    refused(data, 413)                                               # one byte less allowed: refused


# ---- removing identifying and active content (SEC-05) ----

def test_author_metadata_and_comments_are_removed_from_the_stored_file(upload_folder):
    original = make_pdf(pages=2)
    assert SECRET_AUTHOR.encode() in original                        # sanity check: the upload does contain the name
    stored = (upload_folder / uploads.store_pdf(io.BytesIO(original))).read_bytes()
    assert SECRET_AUTHOR.encode() not in stored                      # nowhere in the raw bytes
    reader = PdfReader(io.BytesIO(stored))
    assert len(reader.pages) == 2                                    # the content itself is still there
    assert not reader.metadata or "/Author" not in reader.metadata
    assert all("/Annots" not in page for page in reader.pages)


def test_javascript_is_removed_from_the_stored_file(upload_folder):
    original = make_pdf(with_javascript=True)
    assert b"/JavaScript" in original                                # sanity check
    stored = (upload_folder / uploads.store_pdf(io.BytesIO(original))).read_bytes()
    assert b"/JavaScript" not in stored and b"app.alert" not in stored
    assert "/AA" not in PdfReader(io.BytesIO(stored)).pages[0]


# ---- removing a stored file ----

def test_delete_removes_the_stored_file(upload_folder):
    name = uploads.store_pdf(io.BytesIO(make_pdf()))
    assert uploads.delete_pdf(name) is True
    assert not (upload_folder / name).exists()
    assert uploads.delete_pdf(name) is False                         # already gone, no error


def test_delete_only_accepts_names_made_by_store_pdf(upload_folder, tmp_path):
    upload_folder.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"keep me")
    (upload_folder / "notes.pdf").write_bytes(b"keep me too")
    for bad in ("../outside.pdf", str(outside), "notes.pdf", "", None, 5, "a" * 32 + ".PDF"):
        assert uploads.delete_pdf(bad) is False
    assert outside.exists() and (upload_folder / "notes.pdf").exists()
