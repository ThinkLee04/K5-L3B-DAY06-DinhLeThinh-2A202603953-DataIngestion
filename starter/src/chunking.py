"""Fixed-window and paragraph-aware chunking with stable, traceable IDs."""

import hashlib
import re

from .models import Chunk, DocumentPage


def _make_chunk(page: DocumentPage, text: str, index: int) -> Chunk:
    digest = hashlib.sha256(
        f"{page.document_id}\0{page.page}\0{index}\0{text}".encode("utf-8")
    ).hexdigest()[:24]
    metadata = {**page.metadata, "chunk_index": index}
    return Chunk(
        chunk_id=f"{page.document_id}:{page.page}:{digest}",
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=text,
        metadata=metadata,
    )


def _window_slices(text: str, size: int, overlap: int) -> list[str]:
    if size < 1:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be non-negative and smaller than size")
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(" ", start + int(size * 0.75), end)
            if boundary > start:
                end = boundary
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def fixed_size(page: DocumentPage, size: int, overlap: int) -> list[Chunk]:
    """Split a page into overlapping character windows."""
    return [
        _make_chunk(page, text, index)
        for index, text in enumerate(_window_slices(page.text, size, overlap))
    ]


def _paragraphs(text: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) > 1:
        return blocks

    # PDF text extraction often retains visual line breaks but not paragraph gaps.
    # Reconstruct modest blocks at headings and sentence-ending line boundaries.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return lines
    result: list[str] = []
    current: list[str] = []
    for line in lines:
        is_heading = bool(
            re.match(r"^(?:\d+(?:\.\d+)*\s+|[A-Z]\.\s+|(?:Figure|Fig\.|Table)\s+\d+)", line)
        )
        if is_heading and current:
            result.append(" ".join(current))
            current = []
        current.append(line)
        if re.search(r"[.!?][\]\)\"']?$", line) or len(current) >= 5:
            result.append(" ".join(current))
            current = []
    if current:
        result.append(" ".join(current))
    return result


def by_paragraph(page: DocumentPage, max_size: int) -> list[Chunk]:
    """Group source paragraphs/visual blocks; split oversized blocks safely."""
    if max_size < 1:
        raise ValueError("max_size must be positive")
    blocks: list[str] = []
    pending = ""
    for paragraph in _paragraphs(page.text):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if not paragraph:
            continue
        if len(paragraph) > max_size:
            if pending:
                blocks.append(pending)
                pending = ""
            blocks.extend(_window_slices(paragraph, max_size, 0))
            continue
        candidate = f"{pending}\n\n{paragraph}" if pending else paragraph
        if len(candidate) <= max_size:
            pending = candidate
        else:
            if pending:
                blocks.append(pending)
            pending = paragraph
    if pending:
        blocks.append(pending)
    return [_make_chunk(page, text, index) for index, text in enumerate(blocks)]
