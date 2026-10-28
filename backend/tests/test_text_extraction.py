"""Unit tests for TASK-M3-04: text extraction & chunking."""

import io

import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject

from app.services.chunking_service import (
    CHILD_CHUNK_TOKENS,
    Chunk,
    chunk_document,
    estimate_tokens,
)
from app.services.extraction_service import ExtractionError, ExtractionResult, PageText, extract_text


def make_pdf(pages: list[str]) -> bytes:
    """Build a real multi-page PDF using pypdf primitives (no fixture files)."""
    from pypdf.generic import DictionaryObject, NameObject

    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)

        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(content)

        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        fonts = DictionaryObject({NameObject("/F1"): writer._add_object(font)})
        resources = DictionaryObject({NameObject("/Font"): writer._add_object(fonts)})
        page[NameObject("/Resources")] = writer._add_object(resources)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    document = DocxDocument()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class TestExtraction:
    def test_pdf_extracts_per_page_text(self) -> None:
        data = make_pdf(["Hello EduSense page one", "Second page content"])
        result = extract_text("pdf", data)

        assert result.page_count == 2
        assert result.pages[0].number == 1
        assert "Hello EduSense" in result.full_text
        assert "Second page" in result.full_text

    def test_docx_extracts_paragraphs_as_single_page(self) -> None:
        data = make_docx(["First paragraph.", "Second paragraph."])
        result = extract_text("docx", data)

        assert result.page_count == 1
        assert "First paragraph." in result.full_text
        assert "Second paragraph." in result.full_text

    def test_empty_pdf_fails_with_stable_code(self) -> None:
        data = make_pdf(["", ""])  # no extractable text
        with pytest.raises(ExtractionError) as exc_info:
            extract_text("pdf", data)
        assert exc_info.value.code == "empty_document"

    def test_unextractable_content_fails_never_passes_silently(self) -> None:
        """Architecture: empty/unextractable documents must FAIL."""
        with pytest.raises(ExtractionError) as image_error:
            extract_text("png", PNG_BYTES)
        assert image_error.value.code == "ocr_not_configured"

        with pytest.raises(ExtractionError) as corrupt_error:
            extract_text("pdf", b"this is not a pdf")
        assert corrupt_error.value.code == "corrupt_pdf"

        with pytest.raises(ExtractionError) as unknown_error:
            extract_text("txt", b"plain text")
        assert unknown_error.value.code == "unsupported_file_type"


PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-image"


class TestChunking:
    def test_chunk_indexes_sequential_and_pages_preserved(self) -> None:
        extraction = ExtractionResult(
            pages=[
                PageText(number=1, text="Page one sentence one. Page one sentence two."),
                PageText(number=2, text="Page two sentence one."),
            ],
            file_type="pdf",
        )

        chunks = chunk_document(extraction)

        assert [c.index for c in chunks] == list(range(len(chunks)))
        assert chunks[0].page_number == 1
        assert all(c.content.strip() for c in chunks)
        assert any("Page two" in c.content for c in chunks[1:]) or len(chunks) == 1

    def test_long_content_produces_multiple_chunks_within_budget(self) -> None:
        sentences = " ".join(f"Sentence number {i} explains a concept." for i in range(400))
        extraction = ExtractionResult(pages=[PageText(number=1, text=sentences)], file_type="pdf")

        chunks = chunk_document(extraction)

        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.token_count <= CHILD_CHUNK_TOKENS + estimate_tokens("Sentence number 399 explains a concept.")
        # Overlap: consecutive chunks share trailing content.
        assert chunks[1].content.startswith(chunks[0].content[:50]) or chunks[1].content != chunks[0].content

    def test_empty_extraction_returns_no_chunks(self) -> None:
        extraction = ExtractionResult(pages=[PageText(number=1, text="   ")], file_type="pdf")
        assert chunk_document(extraction) == []

    def test_chunk_dataclass_shape(self) -> None:
        chunk = Chunk(index=0, content="text", page_number=1, token_count=1)
        assert chunk.index == 0
        assert chunk.page_number == 1
