"""Ingestion CLI script."""

import argparse
import asyncio
import hashlib
import os
from pathlib import Path
from typing import List

from app.rag.client import get_client
from app.rag.schema import init_schema
from app.rag.chunker import chunker
from app.rag.embedding import embedding_service

async def process_file(file_path: Path, client):
    """Process a single file."""
    print(f"Processing {file_path.name}...")
    
    with open(file_path, "r", encoding="utf-8") as f:
        text = f.read()

    # Determine source type
    source_type = "manual" if "manual" in file_path.name.lower() else "log" if "log" in file_path.name.lower() else "other"
    
    # Chunking
    chunks = chunker.chunk_text(text)
    
    collection = client.collections.get("KnowledgeChunk")
    
    for i, chunk_text in enumerate(chunks):
        # Generate ID / Checksum
        checksum = hashlib.md5(f"{file_path.name}_{i}_{chunk_text}".encode()).hexdigest()
        
        # Check if exists (idempotency) - optional optimization, 
        # but pure overwrite is fine for now or check by checksum property.
        
        # Get embedding
        vector = await embedding_service.get_embedding(chunk_text)
        if not vector:
            print(f"Failed to get embedding for chunk {i} of {file_path.name}")
            continue

        # Insert
        try:
            collection.data.insert(
                properties={
                    "text": chunk_text,
                    "doc_id": file_path.stem,
                    "source_type": source_type,
                    "section_title": "Unknown", # Parser logic needed for real sections
                    "vehicle_model": "Generic", # Parser logic needed
                    "chunk_index": i,
                    "checksum": checksum,
                },
                vector=vector
            )
            print(f"  Inserted chunk {i}")
        except Exception as e:
            print(f"  Error inserting chunk {i}: {e}")

async def main():
    parser = argparse.ArgumentParser(description="Ingest documents into Weaviate.")
    parser.add_argument("--dir", type=str, required=True, help="Directory containing documents.")
    args = parser.parse_args()
    
    data_dir = Path(args.dir)
    if not data_dir.exists():
        print(f"Directory {data_dir} does not exist.")
        return

    # Init Client and Schema
    try:
        client = get_client()
        init_schema(client)
        print("Schema initialized.")
    except Exception as e:
        print(f"Failed to connect/init schema: {e}")
        return

    # Process files
    files = list(data_dir.glob("*.txt")) + list(data_dir.glob("*.md"))
    print(f"Found {len(files)} files.")
    
    for file_path in files:
        await process_file(file_path, client)
        
    client.close()
    print("Ingestion complete.")

if __name__ == "__main__":
    asyncio.run(main())
