"""Real embedding adapters for OpenAI-compatible, Gemini, and local models."""

from __future__ import annotations

import json
import os
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class Embedder(Protocol):
    model_name: str
    dimensions: int | None

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, question: str) -> list[float]: ...


class MyEmbedder:
    """Provider adapter; errors are surfaced and never replaced by mock vectors."""

    def __init__(self, model_name: str | None = None) -> None:
        self.provider = os.getenv("EMBEDDING_PROVIDER", "").strip().lower()
        if not self.provider:
            raise ValueError(
                "Set EMBEDDING_PROVIDER to openai, openrouter, gemini, or sentence-transformers."
            )
        self.model_name = (model_name or os.getenv("EMBEDDING_MODEL", "")).strip()
        if not self.model_name:
            raise ValueError("Set EMBEDDING_MODEL to a real embedding model name.")
        self.dimensions: int | None = self._configured_dimensions()
        self._local_model = None
        self.request_count = 0
        self.usage_prompt_tokens = 0
        self.usage_total_tokens = 0
        self.usage_requests_reported = 0

        if self.provider in {"openai", "openrouter"}:
            self.api_key = os.getenv("OPENAI_API_KEY" if self.provider == "openai" else "OPENROUTER_API_KEY", "")
            if not self.api_key:
                raise ValueError(f"Set the API key for provider {self.provider} in .env.")
            default_url = (
                "https://api.openai.com/v1/embeddings"
                if self.provider == "openai"
                else "https://openrouter.ai/api/v1/embeddings"
            )
            self.endpoint = os.getenv("EMBEDDING_API_URL", default_url).rstrip("/")
        elif self.provider == "gemini":
            self.api_key = os.getenv("GEMINI_API_KEY", "")
            if not self.api_key:
                raise ValueError("Set GEMINI_API_KEY in .env.")
            self.endpoint = "https://generativelanguage.googleapis.com/v1beta"
        elif self.provider == "sentence-transformers":
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    "Local embeddings require sentence-transformers. Install it with "
                    "`python -m pip install sentence-transformers`."
                ) from exc
            self._local_model = SentenceTransformer(self.model_name)
            self.dimensions = int(self._local_model.get_sentence_embedding_dimension())
        else:
            raise ValueError(
                "Unsupported EMBEDDING_PROVIDER. Choose openai, openrouter, gemini, "
                "or sentence-transformers."
            )

    def _configured_dimensions(self) -> int | None:
        configured = os.getenv("EMBEDDING_DIMENSIONS", "").strip()
        if configured:
            value = int(configured)
            if value < 1:
                raise ValueError("EMBEDDING_DIMENSIONS must be positive.")
            return value
        known = {
            "text-embedding-3-small": 1536,
            "text-embedding-3-large": 3072,
            "text-embedding-ada-002": 1536,
            "gemini-embedding-001": 3072,
        }
        return known.get((os.getenv("EMBEDDING_MODEL", "") or "").split("/")[-1])

    @staticmethod
    def _http_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = Request(url, data=body, headers=headers, method="POST")
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                with urlopen(request, timeout=90) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                last_error = RuntimeError(f"Embedding provider returned HTTP {exc.code}: {detail}")
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                    raise last_error from exc
            except (URLError, TimeoutError) as exc:
                last_error = exc
                if attempt == 3:
                    raise RuntimeError(f"Could not reach embedding provider: {exc}") from exc
            time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"Embedding request failed: {last_error}")

    def _openai_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict = {"model": self.model_name, "input": texts}
        if self.dimensions and self.model_name.endswith(("text-embedding-3-small", "text-embedding-3-large")):
            payload["dimensions"] = self.dimensions
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.provider == "openrouter":
            headers["X-Title"] = "Day 06 Data Ingestion Lab"
            referer = os.getenv("OPENROUTER_HTTP_REFERER")
            if referer:
                headers["HTTP-Referer"] = referer
        response = self._http_json(self.endpoint, payload, headers)
        self.request_count += 1
        usage = response.get("usage") or {}
        if "total_tokens" in usage or "prompt_tokens" in usage:
            self.usage_requests_reported += 1
            self.usage_prompt_tokens += int(usage.get("prompt_tokens", 0))
            self.usage_total_tokens += int(usage.get("total_tokens", usage.get("prompt_tokens", 0)))
        try:
            ordered = sorted(response["data"], key=lambda item: item["index"])
            return [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("Embedding provider response did not contain indexed vectors.") from exc

    def _gemini_batch(self, texts: list[str], task_type: str) -> list[list[float]]:
        model = self.model_name if self.model_name.startswith("models/") else f"models/{self.model_name}"
        url = f"{self.endpoint}/models/{quote(model.removeprefix('models/'))}:batchEmbedContents?key={quote(self.api_key)}"
        payload = {
            "requests": [
                {"model": model, "content": {"parts": [{"text": text}]}, "taskType": task_type}
                for text in texts
            ]
        }
        response = self._http_json(url, payload, {"Content-Type": "application/json"})
        try:
            return [
                [float(value) for value in item["values"]]
                for item in response["embeddings"]
            ]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("Gemini response did not contain embedding values.") from exc

    def _check_vectors(self, vectors: list[list[float]], expected_count: int) -> list[list[float]]:
        if len(vectors) != expected_count:
            raise RuntimeError(f"Expected {expected_count} vectors, received {len(vectors)}.")
        if not vectors or not vectors[0]:
            raise RuntimeError("Embedding provider returned an empty vector.")
        dimensions = len(vectors[0])
        if self.dimensions is not None and self.dimensions != dimensions:
            raise RuntimeError(
                f"Configured embedding dimension {self.dimensions} does not match provider output {dimensions}."
            )
        if any(len(vector) != dimensions for vector in vectors):
            raise RuntimeError("Provider returned vectors with inconsistent dimensions.")
        self.dimensions = dimensions
        return vectors

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        if not texts:
            return []
        if self.provider == "sentence-transformers":
            vectors = self._local_model.encode(
                texts, normalize_embeddings=True, show_progress_bar=False
            ).tolist()
            return self._check_vectors(vectors, len(texts))

        vectors: list[list[float]] = []
        batch_size = 64
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            if self.provider == "gemini":
                vectors.extend(self._gemini_batch(batch, task_type))
            else:
                vectors.extend(self._openai_batch(batch))
        return self._check_vectors(vectors, len(texts))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        task_type = "RETRIEVAL_DOCUMENT" if self.provider == "gemini" else ""
        return self._embed(texts, task_type)

    def embed_query(self, question: str) -> list[float]:
        task_type = "RETRIEVAL_QUERY" if self.provider == "gemini" else ""
        return self._embed([question], task_type)[0]
