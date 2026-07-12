"""Embedding service using a dedicated Ollama embedding model."""

import structlog
import httpx
from typing import List

from app.config import settings

logger = structlog.get_logger(__name__)


class EmbeddingService:
    """Client for generating embeddings via Ollama.

    Uses settings.embedding_model (default: nomic-embed-text) which
    produces 768-dim vectors -- much faster for search and less storage
    than llama3's 4096.  Falls back to settings.llm_endpoint if no
    separate embedding_endpoint is set.

    Reuses a single ``httpx.AsyncClient`` for connection pooling.
    Call :meth:`close` when the service is no longer needed.
    """

    def __init__(self):
        self.base_url = (
            settings.embedding_endpoint or settings.llm_endpoint
        )
        self.model = settings.embedding_model
        self._client: httpx.AsyncClient | None = None
        logger.info(
            "embedding_service.init",
            model=self.model,
            endpoint=self.base_url,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a reusable async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def get_embedding(self, text: str) -> List[float]:
        """Generate embedding vector for a text string.

        Args:
            text: Input text to embed.

        Returns:
            List of floats representing the embedding vector,
            or empty list on error.
        """
        if not text or not text.strip():
            logger.warning(
                "embedding_service.skip_empty",
                model=self.model,
            )
            return []

        client = await self._get_client()
        try:
            response = await client.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": text},
            )
            response.raise_for_status()
            result = response.json()
            embeddings = result.get("embeddings", [])
            embedding = embeddings[0] if embeddings else []
            if not embedding:
                logger.warning(
                    "embedding_service.empty_response",
                    model=self.model,
                    text_len=len(text),
                )
            return embedding
        except Exception as e:
            logger.error(
                "embedding_service.error",
                error=str(e),
                model=self.model,
            )
            return []


# Singleton — one pooled ``httpx.AsyncClient`` for the app's single
# long-lived event loop.  This lifecycle is CORRECT for production;
# do not change it on behalf of test environments.
#
# Guard note (HARNESS-23 T17, issue #160): this singleton breaks
# under pytest-asyncio, where each test runs in its own event loop —
# the pooled client stays bound to the loop that first created it,
# so calls from later loops fail inside ``get_embedding()``'s broad
# exception handler and silently return ``[]`` (observed as
# alternating 0-chunk retrievals on 15/30 RAG eval entries,
# 2026-06-20).  The eval suite therefore embeds via its own
# per-call client:
# ``tests/harness/evals/rag_runner.py::_embed_query``.
# Keep eval-side fixes there; do NOT "fix" this module for tests.
embedding_service = EmbeddingService()
