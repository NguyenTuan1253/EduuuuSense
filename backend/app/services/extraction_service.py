"""Text extraction for uploaded documents (PDF/DOCX/plain; images pending OCR).

Contract (architecture): documents with empty or unextractable content must
FAIL (processing_error_code), never pass silently. Images require an OCR
system dependency that is not part of the Python stack, so they fail with
``ocr_not_configured`` until a PO-approved OCR runtime is provisioned.
"""

import io
from dataclasses import dataclass, field

from docx import Document as DocxDocument
from pypdf import PdfReader
from pypdf.errors import PdfReadError


class ExtractionError(Exception):
    """Raised when content cannot be extracted; carries a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class PageText:
    number: int  # 1-based
    text: str


@dataclass
class ExtractionResult:
    pages: list[PageText] = field(default_factory=list)
    file_type: str = ""

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip())


def extract_text(file_type: str, data: bytes) -> ExtractionResult:
    """Extract per-page text for a supported file type."""
    if file_type == "pdf":
        return _extract_pdf(data)
    if file_type == "docx":
        return _extract_docx(data)
    if file_type in ("png", "jpg", "jpeg"):
        raise ExtractionError(
            "ocr_not_configured",
            "Image OCR requires a runtime dependency that is not configured.",
        )
    raise ExtractionError("unsupported_file_type", f"Unsupported file type: {file_type}")


def _extract_pdf(data: bytes) -> ExtractionResult:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractionError("encrypted_pdf", "Encrypted PDFs cannot be extracted.")
        pages: list[PageText] = []
        for index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            pages.append(PageText(number=index, text=text))
    except ExtractionError:
        raise
    except PdfReadError as exc:
        raise ExtractionError("corrupt_pdf", "The PDF could not be parsed.") from exc
    except Exception as exc:  # noqa: BLE001 — any parse failure means failed status
        raise ExtractionError("corrupt_pdf", "The PDF could not be parsed.") from exc

    if not pages or not any(page.text for page in pages):
        raise ExtractionError("empty_document", "No extractable text found in the PDF.")
    return ExtractionResult(pages=pages, file_type="pdf")


def _extract_docx(data: bytes) -> ExtractionResult:
    try:
        document = DocxDocument(io.BytesIO(data))
        paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    except Exception as exc:  # noqa: BLE001
        raise ExtractionError("corrupt_docx", "The DOCX could not be parsed.") from exc

    if not paragraphs:
        raise ExtractionError("empty_document", "No extractable text found in the DOCX.")
    # DOCX has no page concept; the whole content is one logical "page".
    return ExtractionResult(pages=[PageText(number=1, text="\n".join(paragraphs))], file_type="docx")
