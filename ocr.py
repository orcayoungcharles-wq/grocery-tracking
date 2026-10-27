"""
Receipt OCR via Gemini vision.

Reads API key from (in order):
  1. st.secrets["gemini"]["api_key"]  (Streamlit Cloud / local secrets.toml)
  2. GEMINI_API_KEY environment variable

Uses the REST API so we work even if google-genai is not installed.
Prefers a Flash-Lite / Flash model for free-tier headroom.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# Models that support image input on the free tier (prefer lighter ones first)
_MODEL_CANDIDATES = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-flash-lite-latest",
    "gemini-flash-latest",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
]

_RECEIPT_PROMPT = """You are extracting text from a grocery store receipt photo or scan (Aldi or Kroger).

Return ONLY the plain text of the receipt, preserving line structure as much as possible.
Include:
- store name / address if visible
- every item line with SKU/code, name, quantity, prices, tax codes (FA, FB, NB, F, etc.)
- child quantity lines like "3 x 1.29"
- SUBTOTAL, tax lines, TOTAL, date/time, payment lines

Do not summarize. Do not add commentary. Do not use markdown.
If the image is unreadable, reply with exactly: OCR_FAILED
"""


def _get_api_key() -> Optional[str]:
    # Streamlit secrets (Cloud or .streamlit/secrets.toml)
    try:
        import streamlit as st

        key = st.secrets.get("gemini", {}).get("api_key")
        if key:
            return str(key).strip()
    except Exception:
        pass

    env = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if env:
        return env.strip()
    return None


def has_api_key() -> bool:
    return _get_api_key() is not None


def _mime_for_name(name: str) -> str:
    n = (name or "").lower()
    if n.endswith(".png"):
        return "image/png"
    if n.endswith(".webp"):
        return "image/webp"
    if n.endswith(".gif"):
        return "image/gif"
    if n.endswith(".pdf"):
        return "application/pdf"
    return "image/jpeg"


def _pdf_first_page_png_bytes(pdf_bytes: bytes) -> bytes:
    """Rasterize first PDF page to PNG using pypdfium2 (already in env) or pillow fallback."""
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(pdf_bytes)
        page = doc[0]
        # ~150 DPI equivalent scale
        bitmap = page.render(scale=2.0)
        pil = bitmap.to_pil()
        import io

        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        # Last resort: refuse rather than send a bad PDF
        raise RuntimeError(
            "Could not convert PDF page to image. Upload a JPG/PNG photo instead."
        )


def _call_gemini(image_bytes: bytes, mime: str, api_key: str) -> str:
    """Call Gemini generateContent REST endpoint; try a few model ids."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    body = {
        "contents": [
            {
                "parts": [
                    {"text": _RECEIPT_PROMPT},
                    {"inline_data": {"mime_type": mime, "data": b64}},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 8192,
        },
    }
    payload = json.dumps(body).encode("utf-8")

    last_err = None
    for model in _MODEL_CANDIDATES:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            # Extract text from first candidate
            parts = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [])
            )
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text
            last_err = RuntimeError(f"Empty response from {model}")
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = RuntimeError(f"{model}: HTTP {e.code} — {err_body[:300]}")
            # 404 = model not found → try next; 429 = rate limit → stop
            if e.code == 429:
                break
            continue
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"Gemini OCR failed. Last error: {last_err}")


def extract_text_from_bytes(data: bytes, filename: str = "receipt.jpg") -> str:
    """
    OCR a receipt image or PDF. Returns plain text suitable for the parsers.
    Raises RuntimeError on missing key or API failure.
    """
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "No Gemini API key. Add it under Streamlit Secrets as:\n\n"
            '[gemini]\napi_key = "YOUR_KEY"\n\n'
            "Or set the GEMINI_API_KEY environment variable."
        )

    mime = _mime_for_name(filename)
    if mime == "application/pdf":
        # Convert first page to PNG for broadest model support
        data = _pdf_first_page_png_bytes(data)
        mime = "image/png"

    text = _call_gemini(data, mime, api_key)
    if text.strip() == "OCR_FAILED" or not text.strip():
        raise RuntimeError("OCR could not read this image. Try a clearer photo.")
    return text


def extract_text_from_path(path: str | Path) -> str:
    path = Path(path)
    return extract_text_from_bytes(path.read_bytes(), path.name)
