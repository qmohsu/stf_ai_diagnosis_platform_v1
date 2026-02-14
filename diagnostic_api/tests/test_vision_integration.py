"""Integration tests for VisionService against a real Ollama instance.

These tests require:
  - Ollama running at localhost:11434
  - The llava model pulled: ``ollama pull llava``

Run with:
    python -m pytest diagnostic_api/tests/test_vision_integration.py -v -m integration
"""

import io

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.rag.vision import VisionService

OLLAMA_URL = "http://localhost:11434"
VISION_MODEL = "llava"


def _ollama_reachable() -> bool:
    """Return True if Ollama responds on localhost."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/version", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


def _model_available(model: str) -> bool:
    """Return True if *model* is listed in Ollama's local models."""
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3.0)
        names = [m["name"] for m in r.json().get("models", [])]
        return any(model in n for n in names)
    except Exception:
        return False


# Skip the entire module if infrastructure is missing
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _ollama_reachable(),
        reason="Ollama not reachable at localhost:11434",
    ),
    pytest.mark.skipif(
        not (_ollama_reachable() and _model_available(VISION_MODEL)),
        reason=f"Model '{VISION_MODEL}' not available -- run: ollama pull {VISION_MODEL}",
    ),
]


def _make_wiring_diagram() -> bytes:
    """Generate a synthetic automotive wiring diagram as PNG bytes.

    Draws two labeled boxes (ECU and O2 Sensor) connected by colored
    wires with pin labels -- enough visual detail for a vision model to
    describe meaningfully.
    """
    width, height = 640, 400
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    # Try to use a built-in font; fall back to default bitmap font
    try:
        font = ImageFont.truetype("arial.ttf", 16)
        font_small = ImageFont.truetype("arial.ttf", 12)
    except (OSError, IOError):
        font = ImageFont.load_default()
        font_small = font

    # -- Title --
    draw.text((180, 15), "Wiring Diagram - O2 Sensor", fill="black", font=font)

    # -- ECU box (left) --
    ecu_box = (40, 100, 220, 280)
    draw.rectangle(ecu_box, outline="black", width=2)
    draw.text((100, 110), "ECU", fill="black", font=font)
    draw.text((60, 140), "Connector C1", fill="gray", font=font_small)

    # Pin labels on ECU
    pin_y_start = 170
    pins = [
        ("Pin 1 - 5V Ref", "red"),
        ("Pin 2 - Signal", "green"),
        ("Pin 3 - Ground", "black"),
    ]
    for i, (label, color) in enumerate(pins):
        y = pin_y_start + i * 30
        draw.text((55, y), label, fill=color, font=font_small)
        # Connection point on right edge of ECU box
        draw.ellipse((215, y + 4, 225, y + 14), fill=color)

    # -- O2 Sensor box (right) --
    sensor_box = (420, 100, 600, 280)
    draw.rectangle(sensor_box, outline="black", width=2)
    draw.text((465, 110), "O2 Sensor", fill="black", font=font)
    draw.text((440, 140), "Connector C2", fill="gray", font=font_small)

    # Pin labels on sensor
    for i, (label, color) in enumerate(pins):
        y = pin_y_start + i * 30
        short_label = label.split(" - ")[1]
        draw.text((440, y), short_label, fill=color, font=font_small)
        # Connection point on left edge of sensor box
        draw.ellipse((415, y + 4, 425, y + 14), fill=color)

    # -- Wires connecting ECU to sensor --
    wire_colors = ["red", "green", "black"]
    for i, color in enumerate(wire_colors):
        y = pin_y_start + i * 30 + 9
        draw.line([(225, y), (415, y)], fill=color, width=2)

    # -- Legend --
    draw.text((40, 320), "Wire Colors:", fill="black", font=font_small)
    legend = [("Red = 5V Reference", "red"), ("Green = Signal", "green"), ("Black = Ground", "black")]
    for i, (text, color) in enumerate(legend):
        draw.rectangle((60, 340 + i * 18, 72, 352 + i * 18), fill=color)
        draw.text((78, 338 + i * 18), text, fill="black", font=font_small)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_service() -> VisionService:
    """Create a VisionService pointing at the local Ollama."""
    from unittest.mock import patch, MagicMock

    mock_settings = MagicMock()
    mock_settings.llm_endpoint = OLLAMA_URL
    mock_settings.vision_model = VISION_MODEL

    with patch("app.rag.vision.settings", mock_settings):
        svc = VisionService()
    return svc


class TestVisionIntegration:
    """Integration tests that call the real Ollama vision model."""

    @pytest.mark.asyncio
    async def test_describe_wiring_diagram(self):
        """Vision model returns a meaningful description for a wiring diagram."""
        service = _make_service()
        image_bytes = _make_wiring_diagram()

        try:
            description = await service.describe_image(image_bytes)
        finally:
            await service.close()

        # The model should return a non-trivial description
        assert isinstance(description, str)
        assert len(description) > 50, (
            f"Expected a substantial description, got {len(description)} chars: "
            f"{description!r}"
        )

        # Check that the model picked up on key visual elements.
        # Use case-insensitive matching since model output varies.
        desc_lower = description.lower()
        recognized_terms = [
            "ecu", "sensor", "wire", "wiring", "connector",
            "pin", "signal", "ground", "diagram", "o2",
            "red", "green", "black", "color",
        ]
        matches = [t for t in recognized_terms if t in desc_lower]
        assert len(matches) >= 3, (
            f"Expected at least 3 recognized terms in description, "
            f"found {matches}.\nFull description: {description}"
        )

    @pytest.mark.asyncio
    async def test_describe_with_page_context(self):
        """Passing page context enriches the description."""
        service = _make_service()
        image_bytes = _make_wiring_diagram()
        context = (
            "Section 4.2: Oxygen Sensor Circuit. "
            "The O2 sensor is located in the exhaust manifold downstream of the catalytic converter. "
            "Connector C1 on the ECU side uses a 3-pin Bosch connector."
        )

        try:
            description = await service.describe_image(
                image_bytes, context=context,
            )
        finally:
            await service.close()

        assert isinstance(description, str)
        assert len(description) > 50

    @pytest.mark.asyncio
    async def test_describe_simple_photo(self):
        """Vision model can describe a simple color-block image."""
        # Create a minimal image -- solid blue rectangle
        img = Image.new("RGB", (200, 200), color="blue")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        service = _make_service()
        try:
            description = await service.describe_image(image_bytes)
        finally:
            await service.close()

        assert isinstance(description, str)
        assert len(description) > 0
