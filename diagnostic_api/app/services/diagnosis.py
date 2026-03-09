
import structlog
from app.expert.client import ExpertLLMClient
from app.rag.retrieve import RetrievalService
from app.api.v1.schemas import DiagnosisRequest, DiagnosisResponse

logger = structlog.get_logger()

class DiagnosisService:
    def __init__(self, llm_client: ExpertLLMClient):
        self.llm_client = llm_client
        self.retrieval_service = RetrievalService()

    async def run_diagnosis(self, request: DiagnosisRequest) -> DiagnosisResponse:
        """Execute the full diagnostic pipeline.

        1. RAG Retrieval
        2. Expert Analysis
        """
        logger.info("diagnosis_pipeline_start", vehicle_id=request.vehicle_id)

        symptoms = request.symptoms

        # 1. Retrieval
        # Construct a search query (Vehicle + Symptoms)
        # We include DTC codes in the search query for better precision
        query = f"{request.year} {request.make} {request.model} {symptoms}"
        if request.dtc_codes:
            query += f" {' '.join(request.dtc_codes)}"

        context_results = await self.retrieval_service.retrieve_context(query=query, limit=3)

        if context_results:
            context_str = "\n\n".join(
                [f"[{r.source_type} - {r.doc_id} - {r.section_title}]\n{r.text}" for r in context_results]
            )
            context_used = True
        else:
            context_str = "No specific manual sections or logs found for this issue."
            context_used = False

        # 2. Expert Analysis
        vehicle_info = request.to_vehicle_string()

        # Combine symptoms with DTC codes for the LLM prompt
        full_symptom_description = symptoms
        if request.dtc_codes:
            full_symptom_description += f"\nActive DTCs: {', '.join(request.dtc_codes)}"

        diagnosis = await self.llm_client.generate_diagnosis(
            vehicle_info=vehicle_info,
            symptoms=full_symptom_description,
            context=context_str
        )

        logger.info("diagnosis_pipeline_complete",
                    vehicle_id=request.vehicle_id,
                    risk_count=len(diagnosis.subsystem_risks))

        return DiagnosisResponse(
            diagnosis=diagnosis,
            symptoms=symptoms,
            context_used=context_used
        )
