
import os
import structlog
from typing import Optional
from openai import AsyncOpenAI
from app.expert import prompts, schemas, validate

logger = structlog.get_logger()

class ExpertLLMClient:
    """
    Client for interacting with the Expert Diagnostic Model (hosted on Ollama).
    """

    def __init__(self, base_url: Optional[str] = None, model: str = "llama3"):
        self.base_url = base_url or os.getenv("OLLAMA_BASE_URL", "http://ollama:11434/v1")
        self.model = model
        self.client = AsyncOpenAI(api_key="ollama", base_url=self.base_url)
        logger.info("initialized_expert_llm_client", base_url=self.base_url, model=self.model)

    async def generate_diagnosis(
        self, 
        vehicle_info: str, 
        symptoms: str, 
        context: str
    ) -> schemas.LLMDiagnosisResponse:
        """
        Orchestrate the diagnosis generation process:
        1. Build Prompts
        2. Call LLM
        3. Validate & Parse Output
        """
        
        # 1. Build Prompt
        system_prompt = prompts.SYSTEM_PROMPT
        user_prompt = prompts.USER_PROMPT_TEMPLATE.format(
            vehicle_info=vehicle_info,
            symptoms=symptoms,
            context=context
        )

        logger.info("generating_diagnosis_start", vehicle_info=vehicle_info)

        try:
            # 2. Call LLM
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1, # Low temp for deterministic JSON
                response_format={"type": "json_object"} # Force JSON mode if supported
            )

            raw_content = response.choices[0].message.content
            logger.info("llm_response_received", raw_content_length=len(raw_content))

            # 3. Validate & Parse
            diagnosis = validate.validate_llm_output(raw_content)
            if not diagnosis:
                raise ValueError("Failed to validate LLM output")
            return diagnosis

        except Exception as e:
            logger.error("diagnosis_generation_failed", error=str(e))
            raise
