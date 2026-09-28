"""
extractor.py — Image pre-processing, LLM vision extraction, and OCR fallback.

Pipeline:
  1. Pre-process image with Pillow/OpenCV (grayscale, deskew, contrast).
  2. Call the configured LLM provider with the raw image bytes.
  3. On failure (or no API key), fall back to pytesseract OCR.

Returns an ``ExtractionResult`` dataclass consumed by task_parser.py.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import get_settings
from .utils import get_logger, retry

log = get_logger("extractor")

# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    """Raw output from the extraction stage."""
    raw_text: str
    image_type: str          # e.g. "whiteboard", "receipt", "handwritten note"
    method: str              # "llm" or "ocr"
    source_path: Path = field(default_factory=Path)
    error: Optional[str] = None


# ── Image pre-processing ─────────────────────────────────────────────────────

def preprocess_image(path: Path) -> bytes:
    """
    Return pre-processed image bytes suitable for OCR or LLM input.

    Steps: convert to RGB, grayscale, auto-contrast, deskew.
    Falls back to raw bytes if OpenCV is unavailable.
    """
    try:
        import cv2  # type: ignore
        import numpy as np
        from PIL import Image, ImageOps

        img = Image.open(path).convert("RGB")
        img = ImageOps.autocontrast(img)

        # Convert to numpy for deskew
        cv_img = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2GRAY)
        coords = np.column_stack(np.where(cv_img < 200))
        if coords.size:
            angle = cv2.minAreaRect(coords)[-1]
            if angle < -45:
                angle = 90 + angle
            if abs(angle) > 0.5:
                (h, w) = cv_img.shape
                M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
                cv_img = cv2.warpAffine(
                    cv_img, M, (w, h),
                    flags=cv2.INTER_CUBIC,
                    borderMode=cv2.BORDER_REPLICATE,
                )

        # Re-encode as PNG
        pil_out = Image.fromarray(cv_img)
        buf = io.BytesIO()
        pil_out.save(buf, format="PNG")
        return buf.getvalue()

    except ImportError:
        log.warning("OpenCV not available — using raw image bytes for OCR.")
        return path.read_bytes()
    except Exception as exc:
        log.warning("Pre-processing failed (%s) — using raw bytes.", exc)
        return path.read_bytes()


# ── LLM extraction ────────────────────────────────────────────────────────────

_VISION_PROMPT = (
    "You are an expert at reading and interpreting images. "
    "Look at this image carefully and respond in the following JSON format:\n"
    '{"raw_text": "<all text visible in the image, verbatim>", '
    '"image_type": "<one of: whiteboard, handwritten note, receipt, '
    "screenshot, meeting slide, sticky note, printed document, form, "
    'other>"}\n'
    "Return ONLY valid JSON. No markdown, no explanation."
)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


@retry(attempts=2, backoff_base=2.0, exceptions=(Exception,))
def _call_openai(image_bytes: bytes, model: str, api_key: str) -> dict:
    from openai import OpenAI  # type: ignore
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _VISION_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{_b64(image_bytes)}"
                }},
            ],
        }],
        max_tokens=1024,
    )
    import json
    return json.loads(resp.choices[0].message.content)


@retry(attempts=2, backoff_base=2.0, exceptions=(Exception,))
def _call_anthropic(image_bytes: bytes, model: str, api_key: str) -> dict:
    import anthropic  # type: ignore
    import json
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": _b64(image_bytes),
                    },
                },
                {"type": "text", "text": _VISION_PROMPT},
            ],
        }],
    )
    return json.loads(resp.content[0].text)


@retry(attempts=2, backoff_base=2.0, exceptions=(Exception,))
def _call_watsonx(image_bytes: bytes, model: str, api_key: str,
                  project_id: str, url: str) -> dict:
    """
    Call IBM watsonx.ai via its OpenAI-compatible endpoint.
    Requires ibm-watsonx-ai >= 0.2 or the openai client pointed at the
    watsonx base URL with an IAM token.
    """
    import json
    from openai import OpenAI  # type: ignore
    client = OpenAI(
        api_key=api_key,
        base_url=f"{url.rstrip('/')}/ml/v1/text/chat?version=2024-05-01",
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _VISION_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{_b64(image_bytes)}"
                }},
            ],
        }],
        extra_body={"project_id": project_id},
        max_tokens=1024,
    )
    return json.loads(resp.choices[0].message.content)


def _extract_with_llm(image_bytes: bytes) -> dict:
    """Dispatch to the configured LLM provider."""
    cfg = get_settings()
    provider = cfg.llm.provider.lower()

    if provider == "openai":
        if not cfg.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not set.")
        return _call_openai(image_bytes, cfg.llm.model, cfg.openai_api_key)

    if provider == "anthropic":
        if not cfg.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set.")
        return _call_anthropic(image_bytes, cfg.llm.model, cfg.anthropic_api_key)

    if provider == "watsonx":
        if not cfg.watsonx_api_key:
            raise ValueError("WATSONX_API_KEY is not set.")
        return _call_watsonx(
            image_bytes, cfg.llm.model,
            cfg.watsonx_api_key,
            cfg.llm.watsonx_project_id,
            cfg.llm.watsonx_url or "https://us-south.ml.cloud.ibm.com",
        )

    raise ValueError(f"Unknown LLM provider: {provider!r}")


# ── OCR fallback ──────────────────────────────────────────────────────────────

def _extract_with_ocr(image_bytes: bytes) -> dict:
    """Run Tesseract OCR and return a minimal extraction dict."""
    try:
        import pytesseract  # type: ignore
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img)
        return {"raw_text": text.strip(), "image_type": "unknown (OCR fallback)"}
    except ImportError as exc:
        raise RuntimeError(
            "pytesseract is not installed. Install it or configure an LLM provider."
        ) from exc


# ── Public entry point ────────────────────────────────────────────────────────

def extract(path: Path) -> ExtractionResult:
    """
    Extract text and image type from an image file.

    Tries the configured LLM provider first; falls back to OCR if it fails
    or if no API key is configured and ocr.enabled is True.

    Args:
        path: Absolute path to the image file.

    Returns:
        An :class:`ExtractionResult` with raw text and image type.
    """
    cfg = get_settings()
    image_bytes = preprocess_image(path)
    method = "llm"

    try:
        data = _extract_with_llm(image_bytes)
        return ExtractionResult(
            raw_text=data.get("raw_text", ""),
            image_type=data.get("image_type", "unknown"),
            method="llm",
            source_path=path,
        )
    except Exception as llm_err:
        log.warning("LLM extraction failed (%s).", llm_err)
        if not cfg.ocr.enabled:
            return ExtractionResult(
                raw_text="",
                image_type="unknown",
                method="llm",
                source_path=path,
                error=str(llm_err),
            )

    log.info("Falling back to OCR for %s", path.name)
    try:
        data = _extract_with_ocr(image_bytes)
        return ExtractionResult(
            raw_text=data.get("raw_text", ""),
            image_type=data.get("image_type", "unknown (OCR fallback)"),
            method="ocr",
            source_path=path,
        )
    except Exception as ocr_err:
        log.error("OCR fallback also failed: %s", ocr_err)
        return ExtractionResult(
            raw_text="",
            image_type="unknown",
            method="ocr",
            source_path=path,
            error=str(ocr_err),
        )
