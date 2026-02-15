
import os
import structlog
from typing import AsyncIterator, Optional
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

    def _build_obd_diagnosis_messages(
        self,
        parsed_summary: dict,
        context: str,
    ) -> list[dict]:
        """Build the message list for OBD diagnosis prompts."""
        user_prompt = prompts.OBD_DIAGNOSIS_USER_TEMPLATE.format(
            vehicle_id=parsed_summary.get("vehicle_id", "Unknown"),
            time_range=parsed_summary.get("time_range", "Unknown"),
            dtc_codes=parsed_summary.get("dtc_codes", "None"),
            pid_summary=parsed_summary.get("pid_summary", "N/A"),
            anomaly_events=parsed_summary.get("anomaly_events", "None"),
            diagnostic_clues=parsed_summary.get("diagnostic_clues", "None"),
            context=context or "No additional context retrieved.",
        )
        return [
            {"role": "system", "content": prompts.OBD_DIAGNOSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    async def generate_obd_diagnosis(
        self,
        parsed_summary: dict,
        context: str,
    ) -> str:
        """Generate a free-form markdown OBD diagnosis (Dify workflow style).

        Returns:
            Raw markdown diagnosis text.
        """
        messages = self._build_obd_diagnosis_messages(parsed_summary, context)
        logger.info("obd_diagnosis_start", vehicle_id=parsed_summary.get("vehicle_id"))

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
            )

            content = response.choices[0].message.content
            logger.info("obd_diagnosis_completed", length=len(content))
            return content

        except Exception as e:
            logger.error("obd_diagnosis_failed", error=str(e))
            raise

    async def generate_obd_diagnosis_stream(
        self,
        parsed_summary: dict,
        context: str,
    ) -> AsyncIterator[str]:
        """Stream OBD diagnosis token-by-token.

        Yields:
            Text chunks as the LLM generates them.
        """
        messages = self._build_obd_diagnosis_messages(parsed_summary, context)
        logger.info("obd_diagnosis_stream_start", vehicle_id=parsed_summary.get("vehicle_id"))

        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        except Exception as e:
            logger.error("obd_diagnosis_stream_failed", error=str(e))
            raise
