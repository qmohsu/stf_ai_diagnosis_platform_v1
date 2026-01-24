"""Weaviate client wrapper (v4)."""
import weaviate
from weaviate.classes.init import Auth
from app.config import settings

def get_client() -> weaviate.WeaviateClient:
    """Get Weaviate client (v4)."""
    headers = {}
    if settings.weaviate_api_key:
         # Note: For v4, auth is handled in connect helper, but if custom headers needed:
         pass

    # Parse host and port from URL if possible, or use connect_to_custom
    # settings.weaviate_url is like "http://weaviate:8080"
    
    # Weaviate v4 connection helper
    # Extract host from settings.weaviate_url (e.g. http://weaviate:8080)
    host = settings.weaviate_url.split("://")[1].split(":")[0]
    port = int(settings.weaviate_url.split(":")[2])
    
    client = weaviate.connect_to_custom(
        http_host=host,
        http_port=port,
        http_secure=settings.weaviate_url.startswith("https"),
        grpc_host=host, # Assuming same host for gRPC
        grpc_port=50051, # Default gRPC port inside docker network
        grpc_secure=False,
        headers=headers,
        auth_credentials=Auth.api_key(settings.weaviate_api_key) if settings.weaviate_api_key else None
    )
    return client
