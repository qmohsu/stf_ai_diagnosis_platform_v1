"""Embedding service using Ollama."""

import httpx
from typing import List
from app.config import settings

class EmbeddingService:
    """Client for generating embeddings via Ollama."""
    
    def __init__(self):
        self.base_url = settings.llm_endpoint
        self.model = settings.llm_model 
        # Note: Ideally use an embedding model like 'nomic-embed-text'
        # But if user has llama3, we can try to use it (though it's slow/not ideal for embeddings)
        # OR we assume 'nomic-embed-text' is pulled.
        # Let's stick to the configured model but 'nomic-embed-text' is recommended.

    async def get_embedding(self, text: str) -> List[float]:
        """Generate embedding for text."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}/api/embeddings",
                    json={
                        "model": self.model,
                        "prompt": text
                    },
                    timeout=30.0
                )
                response.raise_for_status()
                result = response.json()
                return result.get("embedding", [])
            except Exception as e:
                print(f"Embedding error: {e}")
                return []

# Singleton
embedding_service = EmbeddingService()
