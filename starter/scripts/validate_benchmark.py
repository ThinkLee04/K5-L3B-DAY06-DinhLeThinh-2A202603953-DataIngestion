"""Check benchmark splits, metadata filters, gold pages, and evidence quotes."""

import csv
import json
import re
import unicodedata
from pathlib import Path

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = ROOT / "benchmark" / "questions.json"
MANIFEST = ROOT / "data" / "manifest.csv"


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def main() -> None:
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    with MANIFEST.open(encoding="utf-8-sig", newline="") as handle:
        manifest = {row["source_id"]: row for row in csv.DictReader(handle)}

    errors = []
    ids = [item["id"] for item in questions]
    if len(questions) < 20 or len(ids) != len(set(ids)):
        errors.append("Expected at least 20 questions with unique IDs.")
    for split, expected in (("dev", 15), ("test", 5)):
        actual = sum(item["split"] == split for item in questions)
        if actual < expected:
            errors.append(f"{split}: expected at least {expected}, found {actual}.")
    filtered = [item for item in questions if item.get("filter")]
    if len(filtered) < 3 or not any(item["split"] == "test" for item in filtered):
        errors.append("Need at least 3 metadata-filter questions, including one test question.")

    readers = {}
    for item in questions:
        source_id = item["gold_source_id"]
        source = manifest.get(source_id)
        if not source:
            errors.append(f"{item['id']}: unknown source_id {source_id}.")
            continue
        relative_path = source["source_path_or_url"].split(" | ", 1)[0]
        if source_id not in readers:
            readers[source_id] = PdfReader(str(ROOT / relative_path))
        reader = readers[source_id]
        page_number = int(item["gold_page"])
        if page_number < 1 or page_number > len(reader.pages):
            errors.append(f"{item['id']}: invalid physical page {page_number}.")
            continue
        included = set()
        for value in source["page_ids_included"].split(";"):
            if "-" in value:
                start, end = map(int, value.split("-", 1))
                included.update(range(start, end + 1))
            elif value:
                included.add(int(value))
        if page_number not in included:
            errors.append(f"{item['id']}: gold page {page_number} is excluded in the manifest.")
        page_text = normalize(reader.pages[page_number - 1].extract_text())
        evidence_quotes = item["evidence_quote"]
        if isinstance(evidence_quotes, str):
            evidence_quotes = [evidence_quotes]
        missing = [quote for quote in evidence_quotes if normalize(quote) not in page_text]
        if missing:
            errors.append(f"{item['id']}: evidence_quote not found on page {page_number}.")

    if errors:
        print("VALIDATION FAILED")
        print("\n".join(errors))
        raise SystemExit(1)
    print(
        f"VALIDATION OK: {len(questions)} questions; "
        f"{sum(item['split'] == 'dev' for item in questions)} dev, "
        f"{sum(item['split'] == 'test' for item in questions)} test; "
        f"{len(filtered)} metadata filters; all gold evidence found on included pages."
    )


if __name__ == "__main__":
    main()
