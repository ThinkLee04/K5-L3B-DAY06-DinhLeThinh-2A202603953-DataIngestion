"""Gold-evidence retrieval metrics and trace serialization contracts."""

import re
import unicodedata
from collections import defaultdict
from typing import Any


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip().casefold()


def _gold_pages(question: dict[str, Any]) -> set[int]:
    pages = question.get("gold_pages", question.get("gold_page"))
    if isinstance(pages, int):
        return {pages}
    return {int(page) for page in (pages or [])}


def _evidence_quotes(question: dict[str, Any]) -> list[str]:
    quotes = question.get("evidence_quote", [])
    return [quotes] if isinstance(quotes, str) else list(quotes)


def _is_evidence_hit(question: dict[str, Any], hit: dict[str, Any]) -> bool:
    if hit.get("metadata", {}).get("source_id") != question.get("gold_source_id"):
        return False
    if int(hit.get("page", -1)) not in _gold_pages(question):
        return False
    text = _normalize(hit.get("text", ""))
    quotes = [_normalize(quote) for quote in _evidence_quotes(question)]
    return bool(quotes) and all(quote in text for quote in quotes)


def evaluate_query(
    question: dict[str, Any], hits: list[dict[str, Any]], strategy: str | None = None
) -> dict[str, Any]:
    """Score strict source/page/evidence matches and retain all retrieved text."""
    ranked = []
    for rank, hit in enumerate(hits[:3], start=1):
        item = {**hit, "rank": rank, "evidence_hit": _is_evidence_hit(question, hit)}
        ranked.append(item)
    requested_filter = question.get("filter")
    applied_filter = requested_filter if strategy == "C_paragraph_filter" else None
    filter_compliant = None
    if applied_filter:
        filter_compliant = bool(ranked) and all(
            all(hit.get("metadata", {}).get(key) == value for key, value in applied_filter.items())
            for hit in ranked
        )
    return {
        "question_id": question["id"],
        "split": question["split"],
        "strategy": strategy,
        "question": question["question"],
        "requested_filter": requested_filter,
        "applied_filter": applied_filter,
        "expected_answer": question["expected_answer"],
        "gold_source_id": question["gold_source_id"],
        "gold_pages": sorted(_gold_pages(question)),
        "evidence_quote": question.get("evidence_quote"),
        "hit_at_1": bool(ranked and ranked[0]["evidence_hit"]),
        "evidence_hit_at_3": any(hit["evidence_hit"] for hit in ranked),
        "filter_compliant_at_3": filter_compliant,
        "retrieved": ranked,
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate automatic retrieval metrics by strategy and split."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trace in traces:
        groups[(trace.get("strategy") or "unknown", trace["split"])].append(trace)
    summary = {}
    for (strategy, split), group in sorted(groups.items()):
        filter_traces = [trace for trace in group if trace.get("applied_filter")]
        summary[f"{strategy}/{split}"] = {
            "question_count": len(group),
            "hit_at_1": sum(bool(trace["hit_at_1"]) for trace in group),
            "hit_at_1_rate": sum(bool(trace["hit_at_1"]) for trace in group) / len(group),
            "evidence_hit_at_3": sum(bool(trace["evidence_hit_at_3"]) for trace in group),
            "evidence_hit_at_3_rate": sum(bool(trace["evidence_hit_at_3"]) for trace in group) / len(group),
            "filtered_question_count": len(filter_traces),
            "filter_compliant": sum(bool(trace["filter_compliant_at_3"]) for trace in filter_traces),
            "filter_compliant_rate": (
                sum(bool(trace["filter_compliant_at_3"]) for trace in filter_traces) / len(filter_traces)
                if filter_traces
                else None
            ),
        }
    return summary
