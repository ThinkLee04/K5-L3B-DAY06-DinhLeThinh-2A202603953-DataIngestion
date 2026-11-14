"""PDF extraction and conservative Unicode/text normalization."""

from pathlib import Path
import re
import unicodedata

from pypdf import PdfReader

from .models import DocumentPage


def normalize_text(text: str) -> str:
    """Normalize Unicode and spacing while retaining paragraph boundaries."""
    text = unicodedata.normalize("NFC", text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00ad", "")
    lines = [re.sub(r"[\t\f\v ]+", " ", line).strip() for line in text.split("\n")]
    compact: list[str] = []
    blank = False
    for line in lines:
        if line:
            compact.append(line)
            blank = False
        elif compact and not blank:
            compact.append("")
            blank = True
    return "\n".join(compact).strip()


def load_pdf(path: Path, document_id: str, source: str) -> list[DocumentPage]:
    """Extract every physical page and retain empty/error pages for auditing."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"PDF not found: {path}")
    reader = PdfReader(str(path), strict=False)
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise ValueError(f"Encrypted PDF cannot be read without a password: {path.name}") from exc

    pages: list[DocumentPage] = []
    for page_number, pdf_page in enumerate(reader.pages, start=1):
        metadata: dict[str, object] = {"pdf_file": path.name, "extraction_status": "ok"}
        try:
            extracted = pdf_page.extract_text()
            text = normalize_text(extracted or "")
            if not text:
                metadata["extraction_status"] = "no_text"
        except Exception as exc:  # preserve the page and report the extraction error
            text = ""
            metadata["extraction_status"] = "error"
            metadata["extraction_error"] = f"{type(exc).__name__}: {exc}"
        pages.append(
            DocumentPage(
                document_id=document_id,
                source=source,
                page=page_number,
                text=text,
                metadata=metadata,
            )
        )
    return pages
