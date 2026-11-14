"""Extract per-page PDF text statistics for the Day 06 corpus manifest."""

import csv
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "manifest.csv"
OUTPUT = ROOT / "data" / "page_audit.csv"

# Human-reviewed page scope for these two PDFs. Mixed pages with substantive
# body text remain included even when references also appear on that page.
INCLUDED_PAGES = {
    "arxiv-2412.05297v1": set(range(2, 23)),  # page 1 is title-only; 23–26 references
    "arxiv-2601.19504v1": set(range(1, 12)),  # page 11 mixes body text and references
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    with MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
        sources = list(csv.DictReader(handle))

    rows = []
    for source in sources:
        pdf_path = ROOT / source["source_path_or_url"].split(" | ", 1)[0]
        reader = PdfReader(str(pdf_path))
        for page_number, page in enumerate(reader.pages, start=1):
            text = normalize(page.extract_text())
            include = page_number in INCLUDED_PAGES.get(source["source_id"], set())
            if include:
                review = "included_body_or_mixed_page"
            elif source["source_id"] == "arxiv-2412.05297v1" and page_number == 1:
                review = "excluded_title_page"
            else:
                review = "excluded_references_only"
            rows.append(
                {
                    "source_id": source["source_id"],
                    "pdf_file": pdf_path.name,
                    "physical_page": page_number,
                    "normalized_character_count": len(text),
                    "included_in_30_page_count": str(include).lower(),
                    "review_hint": review,
                }
            )

    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    for source in sources:
        pages = [row for row in rows if row["source_id"] == source["source_id"]]
        included = [row for row in pages if row["included_in_30_page_count"] == "true"]
        nonempty = sum(row["normalized_character_count"] > 0 for row in included)
        char_count = sum(row["normalized_character_count"] for row in included)
        print(
            f"{source['source_id']}: {len(pages)} physical pages, "
            f"{len(included)} included content pages, {nonempty} extractable, "
            f"{char_count} normalized characters on included pages"
        )
    print(f"Page audit written to {OUTPUT}")


if __name__ == "__main__":
    main()
