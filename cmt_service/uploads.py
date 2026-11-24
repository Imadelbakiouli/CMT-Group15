"""Safe storage of uploaded papers (T08).

Related requirements: SEC-05, SEC-10, NFR-03.

T07 (submissions) calls this and decides what to do with the result:

    file = request.files.get("pdf")
    if file is None:
        return error("A PDF file is required", 400)
    try:
        submission.pdf_path = store_pdf(file)        # a random name like 3f9c...a1.pdf
    except UploadError as e:
        return error(e.message, e.status)
    ...after the new name is committed to the database, remove the old file when replacing:
    delete_pdf(old_name)

What happens to an upload, in this order (every check runs on the server):
1. Size: at most 20 MB, and not empty.                                  -> 413 / 400
2. Type: the content must start with %PDF-. The file name and the
   Content-Type sent by the browser are NOT trusted, we never use them.  -> 415
3. Structure: it must open as a PDF, must not be encrypted and must have
   between 1 and 100 pages.                                              -> 422
4. Cleaning: we do not keep the upload itself. We write a NEW PDF that has only the pages,
   without comments and other annotations, page actions (a place where JavaScript can hide)
   and metadata. So author name, title, tool info and active content stay behind.
5. Storing: random name, created only if it does not exist yet, readable only by the service
   itself, in UPLOAD_DIR. That folder is not served by any route (cmt_service has no /static).

Not possible to remove: names written in the text of the paper itself. Authors must
anonymise their own manuscript. Side effect of step 4: clickable links in the PDF stop working.
"""
import io
import os
import pathlib
import re
import secrets

from pypdf import PdfReader, PdfWriter

MAX_BYTES = 20 * 1024 * 1024    # SEC-10 / NFR-03
MAX_PAGES = 100
PDF_START = b"%PDF-"
STORED_NAME = re.compile(r"[0-9a-f]{32}\.pdf")   # what store_pdf() makes, and the only thing delete_pdf() accepts
PAGE_KEYS_TO_DROP = ("/Annots", "/AA", "/Metadata")   # comments and links, page actions, XMP metadata


class UploadError(Exception):
    """The upload is refused. `status` is the HTTP code to answer with."""

    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def upload_dir():
    # Same default as reviews.py, which reads the PDFs back from this folder
    return pathlib.Path(os.environ.get("UPLOAD_DIR", "uploads")).resolve()


def read_limited(file):
    """Read the upload but never more than the limit (plus one byte, to notice it is too big)."""
    stream = getattr(file, "stream", file)
    data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise UploadError("The PDF is larger than 20 MB", 413)
    if not data:
        raise UploadError("The file is empty", 400)
    return data


def clean_pdf(data):
    """Check the PDF and return a new PDF with only the pages. Raises UploadError if it is not acceptable."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise UploadError("Encrypted PDFs are not accepted", 422)
        pages = len(reader.pages)
        if pages < 1 or pages > MAX_PAGES:
            raise UploadError(f"The PDF must have 1 to {MAX_PAGES} pages", 422)
        writer = PdfWriter()
        for page in reader.pages:
            for key in PAGE_KEYS_TO_DROP:
                if key in page:
                    del page[key]
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        cleaned = out.getvalue()
        if len(PdfReader(io.BytesIO(cleaned)).pages) != pages:   # the result must open again, with all pages
            raise UploadError("This file is not a valid PDF", 422)
        return cleaned
    except UploadError:
        raise
    except Exception:            # a damaged or hostile file can fail in many ways; all mean "no"
        raise UploadError("This file is not a valid PDF", 422)


def store_pdf(file):
    """Check, clean and store an uploaded PDF. Returns the random file name (put it in Submission.pdf_path)."""
    data = read_limited(file)
    if not data.startswith(PDF_START):
        raise UploadError("Only PDF files are accepted", 415)
    cleaned = clean_pdf(data)

    folder = upload_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = secrets.token_hex(16) + ".pdf"          # 128 random bits: cannot be guessed, nothing of the user in it
    path = folder / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # fails instead of overwriting; owner only
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(cleaned)
    except Exception:
        path.unlink(missing_ok=True)               # no half-written file left behind
        raise
    return name


def delete_pdf(name):
    """Remove a stored PDF. Only names that store_pdf() could have made are accepted, anything else is ignored."""
    if not isinstance(name, str) or not STORED_NAME.fullmatch(name):
        return False
    path = upload_dir() / name
    existed = path.is_file()
    path.unlink(missing_ok=True)
    return existed
