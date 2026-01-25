
from fastapi import APIRouter, Depends, HTTPException
from app.services.diagnosis import DiagnosisService
from app.api.v1.schemas import DiagnosisRequest, DiagnosisResponse
from app.expert.client import ExpertLLMClient
import structlog

logger = structlog.get_logger()
router = APIRouter()

# Dependency for the service
def get_diagnosis_service() -> DiagnosisService:
    # In a real app, this client would be a singleton or pooled
    llm_client = ExpertLLMClient()
    return DiagnosisService(llm_client)

@router.post("/", response_model=DiagnosisResponse)
async def create_diagnosis(
    request: DiagnosisRequest,
    service: DiagnosisService = Depends(get_diagnosis_service)
):
    """
    Generate a comprehensive vehicle diagnosis.
    
    1. **Redacts PII** from symptoms.
    2. **Retrieves relevant context** from manuals/logs.
    3. **Analyzes data** using the Expert AI Model.
    """
    try:
        response = await service.run_diagnosis(request)
        return response
    except Exception as e:
        logger.error("diagnosis_endpoint_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
