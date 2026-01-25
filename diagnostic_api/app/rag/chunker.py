"""Text chunking utility."""

from typing import List

class Chunker:
    """Simple text chunker."""
    
    def __init__(self, chunk_size: int = 500, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk_text(self, text: str) -> List[str]:
        """Split text into chunks."""
        # Simple splitting by newlines for now, or fixed size
        # This is a basic implementation. 
        # For production, use recursive character splitter or similar.
        # TODO (OPT-01): Upgrade to Recursive/Semantic Chunking.
        # - Implement LangChain's RecursiveCharacterTextSplitter logic.
        # - Add semantic splitting based on sentence similarity.
        # - Respect Markdown headers (#, ##) to preserve context.
        
        words = text.split()
        chunks = []
        current_chunk = []
        current_count = 0
        
        for word in words:
            current_chunk.append(word)
            current_count += len(word) + 1 # +1 for space
            
            if current_count >= self.chunk_size:
                chunks.append(" ".join(current_chunk))
                # Keep overlap
                overlap_count = 0 
                # Very simple overlap logic: keep last N words that fit in overlap size?
                # Or just simple overlap by count.
                # Let's simple keep last 10 words for overlap to be safe + easy
                keep_words = max(1, int(len(current_chunk) * 0.1))
                current_chunk = current_chunk[-keep_words:]
                current_count = sum(len(w) + 1 for w in current_chunk)
        
        if current_chunk:
            chunks.append(" ".join(current_chunk))
            
        return chunks

# Singleton for easy use
chunker = Chunker()
