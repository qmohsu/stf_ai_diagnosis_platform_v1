"""Embedding service using a dedicated Ollama embedding model."""

import structlog
import httpx
from typing import List

from app.config import settings

logger = structlog.get_logger(__name__)


class EmbeddingService:
    """Client for generating embeddings via Ollama.

    Uses settings.embedding_model (default: nomic-embed-text) which produces
    768-dim vectors -- much faster for search and less storage than llama3's 4096.
    Falls back to settings.llm_endpoint if no separate embedding_endpoint is set.
    """

    def __init__(self):
        self.base_url = settings.embedding_endpoint or settings.llm_endpoint
        self.model = settings.embedding_model
        logger.info(
            "embedding_service.init",
            model=self.model,
            endpoint=self.base_url,
        )

    async def get_embedding(self, text: str) -> List[float]:
        """Generate embedding vector for a text string.

        Args:
            text: Input text to embed.

        Returns:
            List of floats representing the embedding vector, or empty list on error.
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                    timeout=30.0,
                )
                response.raise_for_status()
                result = response.json()
                embedding = result.get("embedding", [])
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


# Singleton
embedding_service = EmbeddingService()
