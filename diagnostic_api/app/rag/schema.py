"""Weaviate schema definitions."""

import structlog
import weaviate
from weaviate.classes.config import Property, DataType, Tokenization

logger = structlog.get_logger(__name__)


def init_schema(client: weaviate.WeaviateClient, force_recreate: bool = False) -> None:
    """Initialize or recreate the KnowledgeChunk collection in Weaviate.

    Args:
        client: Connected Weaviate client.
        force_recreate: If True, delete and recreate the collection
            (required after embedding model or schema changes).
    """
    collection_name = "KnowledgeChunk"

    if force_recreate and client.collections.exists(collection_name):
        logger.warning("schema.force_recreate", collection=collection_name)
        client.collections.delete(collection_name)

    if not client.collections.exists(collection_name):
        logger.info("schema.creating_collection", collection=collection_name)
        client.collections.create(
            name=collection_name,
            properties=[
                Property(
                    name="text",
                    data_type=DataType.TEXT,
                    tokenization=Tokenization.WHITESPACE,
                ),
                Property(
                    name="doc_id",
                    data_type=DataType.TEXT,
                    tokenization=Tokenization.FIELD,
                ),
                Property(
                    name="source_type",
                    data_type=DataType.TEXT,
                    tokenization=Tokenization.FIELD,
                ),
                Property(
                    name="section_title",
                    data_type=DataType.TEXT,
                    tokenization=Tokenization.WORD,
                ),
                Property(
                    name="vehicle_model",
                    data_type=DataType.TEXT,
                    tokenization=Tokenization.FIELD,
                ),
                Property(
                    name="chunk_index",
                    data_type=DataType.INT,
                ),
                Property(
                    name="checksum",
                    data_type=DataType.TEXT,
                    tokenization=Tokenization.FIELD,
                ),
                Property(
                    name="metadata_json",
                    data_type=DataType.TEXT,
                ),
            ],
        )
        logger.info("schema.collection_created", collection=collection_name)
    else:
        logger.info("schema.collection_exists", collection=collection_name)
