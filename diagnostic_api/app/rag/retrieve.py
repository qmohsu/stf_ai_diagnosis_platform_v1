"""Retrieval service logic."""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel

from app.rag.client import get_client
from app.rag.embedding import embedding_service

class RetrievalResult(BaseModel):
    """Retrieval result item."""
    text: str
    score: float
    doc_id: str
    source_type: str
    section_title: str
    chunk_index: int
    metadata: Dict[str, Any] = {}

async def retrieve_context(
    query: str, 
    top_k: int = 3, 
    filters: Optional[Dict[str, Any]] = None
) -> List[RetrievalResult]:
    """Retrieve relevant chunks from Weaviate."""
    
    # 1. Generate embedding
    vector = await embedding_service.get_embedding(query)
    if not vector:
        print("Failed to generate embedding for retrieval.")
        return []

    # 2. Query Weaviate
    client = get_client()
    # Use raw GraphQL for robustness against client version mismatches
    # and to easily get _additional logic
    
    # Build filter clause if needed (omitted for MVP simplicity unless filters provided)
    
    gql_query = f"""
    {{
      Get {{
        KnowledgeChunk(
          nearVector: {{
            vector: {vector}
          }}
          limit: {top_k}
        ) {{
          text
          doc_id
          source_type
          section_title
          chunk_index
          _additional {{
            distance
            score
          }}
        }}
      }}
    }}
    """
    
    try:
        response = client.graphql_raw_query(gql_query)
        # print(f"DEBUG: Weaviate Raw Response: {response}") 
        
        # In Weaviate v4 Python Client, the response object from graphql_raw_query
        # extracts the 'Get' block into a property named .get if it exists.
        # It is NOT a dict, and .get is NOT a method.
        
        chunk_data = None
        
        # Strategy: Access .get attribute directly
        if hasattr(response, 'get'):
             # This attribute contains the content of 'Get' block e.g. {'KnowledgeChunk': [...]}
             chunk_data = response.get
        elif hasattr(response, 'result'):
             # Fallback for other versions
             res = response.result
             if hasattr(res, 'get') and not callable(res.get):
                 chunk_data = res.get
             elif isinstance(res, dict):
                 chunk_data = res.get('data', {}).get('Get')
                 
        if not chunk_data or 'KnowledgeChunk' not in chunk_data:
            print(f"No KnowledgeChunk found in response.")
            return []
            
        results = []
        for obj in chunk_data['KnowledgeChunk']:
            meta = obj.get('_additional', {})
            dist = meta.get('distance', 1.0)
            score = 1.0 - dist if dist is not None else 0.0
            
            results.append(RetrievalResult(
                text=obj.get('text', ""),
                score=score,
                doc_id=obj.get('doc_id', "unknown"),
                source_type=obj.get('source_type', "unknown"),
                section_title=obj.get('section_title', "unknown"),
                chunk_index=obj.get('chunk_index', 0),
                metadata=meta
            ))
            
        return results

        
    except Exception as e:
        print(f"Retrieval error: {e}")
        return []
    finally:
        client.close()
