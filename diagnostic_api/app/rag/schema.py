"""Weaviate schema definitions."""

import weaviate
from weaviate.classes.config import Property, DataType, Tokenization

def init_schema(client: weaviate.WeaviateClient) -> None:
    """Initialize Weaviate schema."""
    
    # Define KnowledgeChunk collection (single collection for both manuals and logs for easier search)
    # or separate. Let's use a single generic 'KnowledgeChunk' with a 'source_type' field.
    
    if not client.collections.exists("KnowledgeChunk"):
        client.collections.create(
            name="KnowledgeChunk",
            properties=[
                Property(name="text", data_type=DataType.TEXT, tokenization=Tokenization.WHITESPACE),
                Property(name="doc_id", data_type=DataType.TEXT, tokenization=Tokenization.FIELD),
                Property(name="source_type", data_type=DataType.TEXT), # duplicate, manual, log
                Property(name="section_title", data_type=DataType.TEXT),
                Property(name="vehicle_model", data_type=DataType.TEXT),
                Property(name="chunk_index", data_type=DataType.INT),
                Property(name="checksum", data_type=DataType.TEXT),
                Property(name="metadata_json", data_type=DataType.TEXT), # extra flexibility
            ]
        )
