"""
test_extractor.py — Unit tests for src/extractor.py.

All LLM and OCR calls are mocked; no real API keys or Tesseract required.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.extractor import (
    ExtractionResult,
    extract,
    preprocess_image,
)


# ── preprocess_image ──────────────────────────────────────────────────────────

def test_preprocess_image_returns_bytes(tmp_dir):
    """preprocess_image should return bytes even if OpenCV is not available."""
    # Create a tiny valid PNG (1x1 white pixel)
    from PIL import Image
    img_path = tmp_dir / "test.png"
    Image.new("RGB", (1, 1), color=(255, 255, 255)).save(img_path)

    result = preprocess_image(img_path)
    assert isinstance(result, bytes)
    assert len(result) > 0


# ── LLM extraction ────────────────────────────────────────────────────────────

class TestExtractWithLLM:
    """Tests for the LLM vision extraction path."""

    @patch("src.extractor.get_settings")
    @patch("src.extractor._call_openai")
    def test_openai_success(self, mock_call, mock_cfg, tmp_dir):
        """Successful OpenAI response returns an ExtractionResult with method='llm'."""
        mock_cfg.return_value = MagicMock(
            llm=MagicMock(provider="openai", model="gpt-4o"),
            openai_api_key="test-key",
            ocr=MagicMock(enabled=False),
        )
        mock_call.return_value = {
            "raw_text": "Call Alice about the project",
            "image_type": "sticky note",
        }

        from PIL import Image
        img_path = tmp_dir / "sticky.png"
        Image.new("RGB", (10, 10)).save(img_path)

        with patch("src.extractor.preprocess_image", return_value=b"fake"):
            result = extract(img_path)

        assert result.method == "llm"
        assert result.image_type == "sticky note"
        assert "Alice" in result.raw_text
        assert result.error is None

    @patch("src.extractor.get_settings")
    @patch("src.extractor._call_openai", side_effect=RuntimeError("timeout"))
    @patch("src.extractor._extract_with_ocr")
    def test_falls_back_to_ocr_on_llm_failure(self, mock_ocr, mock_llm, mock_cfg, tmp_dir):
        """When LLM fails and OCR is enabled, OCR result is returned."""
        mock_cfg.return_value = MagicMock(
            llm=MagicMock(provider="openai", model="gpt-4o"),
            openai_api_key="test-key",
            ocr=MagicMock(enabled=True),
        )
        mock_ocr.return_value = {
            "raw_text": "fallback text",
            "image_type": "unknown (OCR fallback)",
        }

        from PIL import Image
        img_path = tmp_dir / "img.png"
        Image.new("RGB", (10, 10)).save(img_path)

        with patch("src.extractor.preprocess_image", return_value=b"fake"):
            result = extract(img_path)

        assert result.method == "ocr"
        assert result.raw_text == "fallback text"
        mock_ocr.assert_called_once()

    @patch("src.extractor.get_settings")
    @patch("src.extractor._call_openai", side_effect=RuntimeError("timeout"))
    def test_returns_error_result_when_ocr_disabled(self, mock_llm, mock_cfg, tmp_dir):
        """When LLM fails and OCR is disabled, error is surfaced in the result."""
        mock_cfg.return_value = MagicMock(
            llm=MagicMock(provider="openai", model="gpt-4o"),
            openai_api_key="test-key",
            ocr=MagicMock(enabled=False),
        )

        from PIL import Image
        img_path = tmp_dir / "img.png"
        Image.new("RGB", (10, 10)).save(img_path)

        with patch("src.extractor.preprocess_image", return_value=b"fake"):
            result = extract(img_path)

        assert result.method == "llm"
        assert result.error is not None
        assert result.raw_text == ""
