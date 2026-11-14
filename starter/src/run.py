"""Run the Day 06 ingestion, retrieval, and benchmark pipeline."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
import math
import statistics
from pathlib import Path
import time
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import load_pdf
from .models import Chunk, DocumentPage
from .store import PgVectorStore


ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ("A_fixed", "B_paragraph", "C_paragraph_filter")


def _parse_pages(spec: str) -> set[int]:
    pages: set[int] = set()
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, last = (int(value) for value in part.split("-", 1))
            if first < 1 or last < first:
                raise ValueError(f"Invalid page range in manifest: {part}")
            pages.update(range(first, last + 1))
        else:
            pages.add(int(part))
    return pages


def _read_manifest() -> list[dict[str, str]]:
    path = ROOT / "data" / "manifest.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"No corpus sources in {path}")
    return rows


def _load_corpus(manifest: list[dict[str, str]]) -> tuple[list[DocumentPage], dict[str, dict[str, Any]]]:
    pages: list[DocumentPage] = []
    page_counts: dict[str, int] = {}
    included_count = 0
    source_meta: dict[str, dict[str, Any]] = {}
    for row in manifest:
        source_id = row["source_id"]
        source_path = row["source_path_or_url"].split(" | ", 1)[0]
        pdf_path = ROOT / source_path
        included_pages = _parse_pages(row["page_ids_included"])
        source = row["source_path_or_url"].split(" | ")[-1]
        all_pages = load_pdf(pdf_path, source_id, source)
        if int(row["physical_page_count"]) != len(all_pages):
            raise ValueError(
                f"{source_id}: manifest says {row['physical_page_count']} physical pages, "
                f"but PDF has {len(all_pages)}. Update the manifest before ingestion."
            )
        source_meta[source_id] = {
            "source_id": source_id,
            "title": row["title"],
            "authors": row.get("authors", ""),
            "year": int(row["year"]),
            "document_type": row.get("document_type", ""),
            "subject": row.get("subject", ""),
            "language": row.get("language", ""),
            "version": row.get("version", ""),
            "license_or_permission": row.get("license_or_permission", ""),
            "source_path": source_path,
        }
        for page in all_pages:
            if page.page not in included_pages:
                continue
            if not page.text:
                status = page.metadata.get("extraction_status")
                detail = page.metadata.get("extraction_error", "page has no extractable text")
                raise ValueError(f"{source_id} physical page {page.page}: {status} ({detail}). OCR or audit it.")
            page.metadata.update(source_meta[source_id])
            pages.append(page)
            included_count += 1
        page_counts[source_id] = len(included_pages)

    if included_count < 30:
        raise ValueError(f"Corpus contains only {included_count} included content pages; at least 30 are required.")
    return pages, {"per_source": page_counts, "included_content_pages": included_count, "sources": source_meta}


def _build_chunks(pages: list[DocumentPage], fixed_size_chars: int, overlap: int, paragraph_max: int):
    strategies: dict[str, list[Chunk]] = {name: [] for name in STRATEGIES}
    for page in pages:
        strategies["A_fixed"].extend(fixed_size(page, fixed_size_chars, overlap))
        paragraph_chunks = by_paragraph(page, paragraph_max)
        strategies["B_paragraph"].extend(paragraph_chunks)
        # B and C deliberately share identical chunks; only query filtering changes.
        strategies["C_paragraph_filter"].extend(paragraph_chunks)
    for chunks in strategies.values():
        for chunk in chunks:
            if not chunk.text.strip():
                raise ValueError(f"Chunk {chunk.chunk_id} is empty")
    return strategies


def _embed_chunks(embedder: MyEmbedder, chunks: list[Chunk], cache: dict[str, list[float]]) -> list[list[float]]:
    missing = list(dict.fromkeys(chunk.text for chunk in chunks if chunk.text not in cache))
    if missing:
        vectors = embedder.embed_documents(missing)
        if len(vectors) != len(missing):
            raise RuntimeError("Embedding provider returned an incomplete batch.")
        cache.update(zip(missing, vectors))
    return [cache[chunk.text] for chunk in chunks]


def _chunk_stats(chunks: list[Chunk]) -> dict[str, Any]:
    lengths = sorted(len(chunk.text) for chunk in chunks)
    if not lengths:
        return {"count": 0, "total_characters": 0, "min_characters": 0, "median_characters": 0, "p95_characters": 0, "max_characters": 0}
    return {
        "count": len(lengths),
        "total_characters": sum(lengths),
        "min_characters": lengths[0],
        "median_characters": statistics.median(lengths),
        "p95_characters": lengths[max(0, math.ceil(0.95 * len(lengths)) - 1)],
        "max_characters": lengths[-1],
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_manual_templates(
    run_dir: Path,
    chunks_by_strategy: dict[str, list[Chunk]],
    traces: list[dict[str, Any]],
    pages: list[DocumentPage],
) -> None:
    coherence_path = run_dir / "manual_coherence_template.csv"
    sorted_positions = sorted({(page.document_id, page.page) for page in pages})[:10]
    page_lookup = {(page.document_id, page.page): page for page in pages}
    with coherence_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "sample_id", "source_id", "page", "position_rule", "target_excerpt",
            "strategy", "chunk_id", "chunk_text", "target_excerpt_found",
            "score_0_to_2", "rationale",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for sample_index, (source_id, page_number) in enumerate(sorted_positions, start=1):
            page = page_lookup[(source_id, page_number)]
            text = page.text
            start = max(0, len(text) // 2 - 60)
            target_excerpt = text[start : min(len(text), start + 120)]
            normalized_target = " ".join(target_excerpt.casefold().split())
            for strategy, chunks in chunks_by_strategy.items():
                candidates = [chunk for chunk in chunks if chunk.document_id == source_id and chunk.page == page_number]
                for chunk in candidates:
                    normalized_chunk = " ".join(chunk.text.casefold().split())
                    writer.writerow(
                        {
                            "sample_id": f"coh-{sample_index:02d}",
                            "source_id": source_id,
                            "page": page_number,
                            "position_rule": "120 characters centered on the page midpoint; selected before comparing strategies",
                            "target_excerpt": target_excerpt,
                            "strategy": strategy,
                            "chunk_id": chunk.chunk_id,
                            "chunk_text": chunk.text,
                            "target_excerpt_found": str(normalized_target in normalized_chunk).lower(),
                            "score_0_to_2": "",
                            "rationale": "",
                        }
                    )

    grounding_path = run_dir / "manual_grounding_template.csv"
    test_traces = [trace for trace in traces if trace["split"] == "test"]
    with grounding_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["question_id", "strategy", "question", "gold_source_id", "gold_pages", "top3_chunk_ids", "top3_text", "grounding_0_to_2", "rationale"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trace in test_traces:
            writer.writerow(
                {
                    "question_id": trace["question_id"],
                    "strategy": trace["strategy"],
                    "question": trace["question"],
                    "gold_source_id": trace["gold_source_id"],
                    "gold_pages": ";".join(map(str, trace["gold_pages"])),
                    "top3_chunk_ids": ";".join(hit["chunk_id"] for hit in trace["retrieved"]),
                    "top3_text": "\n---\n".join(hit["text"] for hit in trace["retrieved"]),
                    "grounding_0_to_2": "",
                    "rationale": "",
                }
            )

    metadata_path = run_dir / "metadata_trace_audit.csv"
    with metadata_path.open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "question_id", "strategy", "rank", "chunk_id", "retrieved_source_id",
            "retrieved_page", "metadata_source_id", "metadata_source", "metadata_page",
            "metadata_year", "source_id_matches", "page_matches", "filter", "filter_matches",
            "manual_usefulness_notes",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trace in test_traces:
            for hit in trace["retrieved"]:
                metadata = hit.get("metadata", {})
                applied_filter = trace.get("applied_filter") or {}
                filter_matches = all(metadata.get(key) == value for key, value in applied_filter.items())
                writer.writerow(
                    {
                        "question_id": trace["question_id"],
                        "strategy": trace["strategy"],
                        "rank": hit["rank"],
                        "chunk_id": hit["chunk_id"],
                        "retrieved_source_id": metadata.get("source_id"),
                        "retrieved_page": hit.get("page"),
                        "metadata_source_id": metadata.get("source_id"),
                        "metadata_source": metadata.get("source"),
                        "metadata_page": metadata.get("page"),
                        "metadata_year": metadata.get("year"),
                        "source_id_matches": str(metadata.get("source_id") == hit.get("document_id")).lower(),
                        "page_matches": str(metadata.get("page") == hit.get("page")).lower(),
                        "filter": json.dumps(applied_filter, ensure_ascii=False) if applied_filter else "",
                        "filter_matches": str(filter_matches).lower() if applied_filter else "N/A",
                        "manual_usefulness_notes": "",
                    }
                )


def main() -> None:
    load_dotenv(ROOT / ".env")
    manifest = _read_manifest()
    questions_path = ROOT / "benchmark" / "questions.json"
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    if len(questions) < 20:
        raise ValueError("Benchmark must contain at least 20 questions.")
    for split, minimum in (("dev", 15), ("test", 5)):
        if sum(question.get("split") == split for question in questions) < minimum:
            raise ValueError(f"Benchmark needs at least {minimum} {split} questions.")

    ingestion_started = time.monotonic()
    pages, corpus_info = _load_corpus(manifest)
    ingestion_seconds = time.monotonic() - ingestion_started
    fixed_size_chars = int(os.getenv("CHUNK_SIZE", "500"))
    overlap = int(os.getenv("CHUNK_OVERLAP", "50"))
    paragraph_max = int(os.getenv("PARAGRAPH_MAX_SIZE", "850"))
    chunking_started = time.monotonic()
    chunks_by_strategy = _build_chunks(pages, fixed_size_chars, overlap, paragraph_max)
    chunking_seconds = time.monotonic() - chunking_started

    database_url = os.getenv("DATABASE_URL", "")
    embedder = MyEmbedder()
    store = PgVectorStore(database_url, embedder.dimensions)
    store.create_schema()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ROOT / "results" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    start_time = time.monotonic()
    embedding_cache: dict[str, list[float]] = {}
    query_cache: dict[str, list[float]] = {}
    all_traces: list[dict[str, Any]] = []
    chunk_counts: dict[str, int] = {}

    for strategy, chunks in chunks_by_strategy.items():
        print(f"[{strategy}] embedding {len(chunks)} chunks")
        vectors = _embed_chunks(embedder, chunks, embedding_cache)
        if embedder.dimensions is None:
            raise RuntimeError("Embedding dimension was not detected from the provider response.")
        if store.dimensions is None:
            store.dimensions = embedder.dimensions
        store.upsert(strategy, chunks, vectors)
        chunk_counts[strategy] = len(chunks)

        for question in questions:
            query = question["question"]
            if query not in query_cache:
                query_cache[query] = embedder.embed_query(query)
            filters = question.get("filter") if strategy == "C_paragraph_filter" else None
            hits = store.search(strategy, query_cache[query], top_k=3, filters=filters)
            all_traces.append(evaluate_query(question, hits, strategy=strategy))
        print(f"[{strategy}] stored and retrieved {len(questions)} queries")

    elapsed = time.monotonic() - start_time
    trace_path = run_dir / "traces.jsonl"
    with trace_path.open("w", encoding="utf-8", newline="\n") as handle:
        for trace in all_traces:
            handle.write(json.dumps(trace, ensure_ascii=False) + "\n")

    summary = summarize(all_traces)
    _write_json(run_dir / "summary.json", summary)
    provider = embedder.provider
    usage_complete = (
        embedder.request_count > 0
        and embedder.usage_requests_reported == embedder.request_count
    )
    prompt_tokens = embedder.usage_prompt_tokens if usage_complete else None
    total_tokens = embedder.usage_total_tokens if usage_complete else None
    cost_per_million_tokens = (
        0.01
        if provider == "openrouter" and embedder.model_name == "qwen/qwen3-embedding-8b"
        else None
    )
    estimated_cost = (
        round(total_tokens * cost_per_million_tokens / 1_000_000, 8)
        if total_tokens is not None and cost_per_million_tokens is not None
        else (0 if provider == "sentence-transformers" else None)
    )
    run_info = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "embedding_provider": provider,
        "embedding_model": embedder.model_name,
        "embedding_dimensions": embedder.dimensions,
        "vector_store": "PostgreSQL + pgvector; exact cosine distance (<=>); JSONB metadata filter",
        "corpus": corpus_info,
        "question_count": len(questions),
        "question_splits": {
            "dev": sum(question["split"] == "dev" for question in questions),
            "test": sum(question["split"] == "test" for question in questions),
        },
        "chunk_counts": chunk_counts,
        "chunk_statistics": {strategy: _chunk_stats(chunks) for strategy, chunks in chunks_by_strategy.items()},
        "chunk_settings": {
            "A_fixed": {"size_characters": fixed_size_chars, "overlap_characters": overlap},
            "B_paragraph": {"max_characters": paragraph_max},
            "C_paragraph_filter": {"max_characters": paragraph_max, "filter": "question-provided metadata only"},
        },
        "query_filter_policy": "Only C applies the filter specified in each benchmark question; A and B never filter.",
        "elapsed_seconds_embedding_storage_retrieval": round(elapsed, 3),
        "elapsed_seconds_pdf_ingestion": round(ingestion_seconds, 3),
        "elapsed_seconds_chunking": round(chunking_seconds, 3),
        "token_count": total_tokens,
        "token_count_note": "Provider-reported embedding request tokens; null if the provider omits usage. Chunk character statistics are recorded separately.",
        "provider_reported_prompt_tokens": prompt_tokens,
        "provider_reported_total_tokens": total_tokens,
        "provider_usage_complete": usage_complete,
        "cost_per_million_tokens_usd": cost_per_million_tokens,
        "estimated_cost_usd": estimated_cost,
        "cost_note": "OpenRouter token counts are provider-reported; the estimate uses the Qwen3 Embedding 8B rate of $0.01 per million tokens. Confirm final billing in the provider dashboard.",
        "outputs": [
            "traces.jsonl", "summary.json", "manual_coherence_template.csv",
            "manual_grounding_template.csv", "metadata_trace_audit.csv",
        ],
    }
    _write_json(run_dir / "run_info.json", run_info)
    _write_manual_templates(run_dir, chunks_by_strategy, all_traces, pages)
    (ROOT / "results").mkdir(parents=True, exist_ok=True)
    (ROOT / "results" / "latest_run.txt").write_text(run_id + "\n", encoding="utf-8")
    print(f"Run complete: {run_dir}")
    print(f"Elapsed embedding/storage/retrieval time: {elapsed:.1f}s")
    for key, values in summary.items():
        print(
            f"{key}: Hit@1 {values['hit_at_1']}/{values['question_count']}; "
            f"Evidence Hit@3 {values['evidence_hit_at_3']}/{values['question_count']}"
        )


if __name__ == "__main__":
    main()
