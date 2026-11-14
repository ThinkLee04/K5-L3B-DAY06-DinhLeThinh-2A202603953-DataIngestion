"""PostgreSQL + pgvector storage with exact cosine search and JSONB filters."""

import json
import math
from typing import Any

from .models import Chunk


class PgVectorStore:
    ALLOWED_FILTERS = {"year", "document_type", "subject", "language", "version", "section"}

    def __init__(self, database_url: str, dimensions: int | None = None) -> None:
        if not database_url:
            raise ValueError("DATABASE_URL is required.")
        if dimensions is not None and dimensions < 1:
            raise ValueError("dimensions must be positive when specified")
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("Install database dependencies with `python -m pip install -r requirements.txt`.") from exc
        self.database_url = database_url
        self.dimensions = dimensions

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)

    def create_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS retrieval_chunks (
                    strategy TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
                    embedding VECTOR NOT NULL,
                    PRIMARY KEY (strategy, chunk_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS retrieval_chunks_strategy_idx ON retrieval_chunks (strategy)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS retrieval_chunks_metadata_idx ON retrieval_chunks USING GIN (metadata)"
            )

    @staticmethod
    def _vector_literal(vector: list[float]) -> str:
        if not vector:
            raise ValueError("Embedding vectors cannot be empty.")
        if any(not math.isfinite(float(value)) for value in vector):
            raise ValueError("Embedding vectors must contain only finite numbers.")
        return "[" + ",".join(repr(float(value)) for value in vector) + "]"

    def upsert(self, strategy: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if not strategy:
            raise ValueError("strategy is required")
        if len(chunks) != len(vectors):
            raise ValueError(f"Chunk/vector count differs: {len(chunks)} chunks, {len(vectors)} vectors.")
        if not chunks:
            return
        if not vectors:
            raise ValueError("Embedding vectors are required when chunks are provided.")
        lengths = {len(vector) for vector in vectors}
        if len(lengths) != 1:
            raise ValueError("All vectors must have the same dimension.")
        actual_dimensions = lengths.pop()
        if self.dimensions is not None and actual_dimensions != self.dimensions:
            raise ValueError(
                f"Vector dimension {actual_dimensions} does not match configured {self.dimensions}."
            )
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise ValueError("Chunk IDs must be unique within an upsert batch.")
        for chunk, vector in zip(chunks, vectors):
            if not chunk.text.strip() or not chunk.document_id or not chunk.source or chunk.page < 1:
                raise ValueError(f"Chunk {chunk.chunk_id} has incomplete traceability metadata.")
            if len(vector) != actual_dimensions:
                raise ValueError(f"Invalid vector size for chunk {chunk.chunk_id}.")

        by_document: dict[str, list[tuple]] = {}
        for chunk, vector in zip(chunks, vectors):
            metadata = {
                **chunk.metadata,
                "document_id": chunk.document_id,
                "source": chunk.source,
                "page": chunk.page,
            }
            by_document.setdefault(chunk.document_id, []).append(
                (
                    strategy,
                    chunk.chunk_id,
                    chunk.document_id,
                    chunk.source,
                    chunk.page,
                    chunk.text,
                    json.dumps(metadata, ensure_ascii=False),
                    self._vector_literal(vector),
                )
            )

        with self._connect() as connection:
            cursor = connection.cursor()
            for document_id, rows in by_document.items():
                # Replace the complete document snapshot so removed/changed chunks do not linger.
                cursor.execute(
                    "DELETE FROM retrieval_chunks WHERE strategy = %s AND document_id = %s",
                    (strategy, document_id),
                )
                cursor.executemany(
                    """
                    INSERT INTO retrieval_chunks
                        (strategy, chunk_id, document_id, source, page, text, metadata, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector)
                    """,
                    rows,
                )

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if self.dimensions is not None and len(query_vector) != self.dimensions:
            raise ValueError(
                f"Query vector dimension {len(query_vector)} does not match configured {self.dimensions}."
            )
        filters = filters or {}
        invalid = set(filters) - self.ALLOWED_FILTERS
        if invalid:
            raise ValueError(f"Unsupported metadata filter keys: {', '.join(sorted(invalid))}")
        vector_literal = self._vector_literal(query_vector)
        filter_json = json.dumps(filters, ensure_ascii=False)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chunk_id, document_id, source, page, text, metadata,
                       1 - (embedding <=> %s::vector) AS score
                FROM retrieval_chunks
                WHERE strategy = %s AND metadata @> %s::jsonb
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                (vector_literal, strategy, filter_json, vector_literal, top_k),
            ).fetchall()
        return [
            {
                "chunk_id": row[0],
                "document_id": row[1],
                "source": row[2],
                "page": row[3],
                "text": row[4],
                "metadata": row[5],
                "score": float(row[6]),
            }
            for row in rows
        ]
