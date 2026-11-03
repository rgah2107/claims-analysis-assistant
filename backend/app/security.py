"""Upload checks and log redaction.

The allow-list is extension plus magic bytes. A renamed ZIP is not a DOCX
unless it actually contains ``word/document.xml``. Logs get a second pass that
masks emails, phone numbers, and Spanish national-id shapes. Document text is
not written to logs in the first place; this filter is the backstop.
"""

from __future__ import annotations

import io
import re
import zipfile

from app.errors import UploadRejected

PDF_MAGIC = b"%PDF"
DOCX_MARK = "word/document.xml"

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<!\w)(?:\+34[\s.-]?)?[6-9]\d{2}[\s.-]?\d{3}[\s.-]?\d{3}(?!\w)")
DNI_RE = re.compile(r"\b\d{8}[A-Z]\b", re.IGNORECASE)
NIE_RE = re.compile(r"\b[XYZ]\d{7}[A-Z]\b", re.IGNORECASE)
CLAIM_NUMBER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def validate_claim_number(claim_number: str) -> str:
    """Reject path-like claim numbers before they are used in a directory."""
    if not CLAIM_NUMBER_RE.fullmatch(claim_number or ""):
        raise UploadRejected(
            "A claim id may only use letters, digits, '.', '_' and '-'."
        )
    return claim_number


def validate_upload(filename: str, data: bytes, max_bytes: int) -> str:
    """Return a safe file name, or raise ``UploadRejected`` before anything is stored.

    The whole batch is rejected when one file fails. A partial 202 would hide
    a file the adjuster thinks was accepted.
    """
    safe_name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not safe_name or safe_name in {".", ".."} or len(safe_name) > 180:
        raise UploadRejected("Invalid file name.")
    lowered = safe_name.lower()
    if not data:
        raise UploadRejected(f"{safe_name}: the file is empty.")
    if len(data) > max_bytes:
        raise UploadRejected(f"{safe_name}: exceeds the maximum of {max_bytes} bytes.")
    if lowered.endswith(".pdf"):
        if not data.startswith(PDF_MAGIC):
            raise UploadRejected(f"{safe_name}: the contents are not a PDF.")
        return safe_name
    if lowered.endswith(".docx"):
        if not data.startswith(b"PK"):
            raise UploadRejected(f"{safe_name}: the contents are not a DOCX.")
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile as exc:
            raise UploadRejected(f"{safe_name}: corrupt DOCX.") from exc
        if DOCX_MARK not in names:
            raise UploadRejected(f"{safe_name}: the ZIP does not contain a Word document.")
        return safe_name
    raise UploadRejected(f"{safe_name}: only PDF and DOCX files are accepted.")


def redact(value: str) -> str:
    """Replace contact details and id numbers with fixed tokens."""
    masked = EMAIL_RE.sub("[email]", value)
    masked = PHONE_RE.sub("[phone]", masked)
    masked = DNI_RE.sub("[dni]", masked)
    masked = NIE_RE.sub("[nie]", masked)
    return masked
