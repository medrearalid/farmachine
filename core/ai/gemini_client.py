"""
Gemini API client (google-genai) for Metin2 CAPTCHA solving.

The solver expects a single cropped CAPTCHA dialog image that contains:
- 2x3 image grid
- bottom instruction text

It returns an integer 1..6 for grid position:
1=top-left, 2=top-middle, 3=top-right,
4=bottom-left, 5=bottom-middle, 6=bottom-right.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import List, Optional

from dotenv import load_dotenv
from google import genai
from PIL import Image

from core.utils.path_util import resource_path

_CAPTCHA_PROMPT = (
    "This is a game CAPTCHA. Below a 2x3 grid of images (6 total), there is a Turkish instruction. "
    "The instruction format is usually: 'Resimler arasından [TARGET] resmini seçiniz.' "
    "Your goal: Read the [TARGET] text from the bottom instruction (it is usually 2 characters, like 'q9', 'nO', 'ex'). "
    "Then, find the matching text within the 6 images in the 2x3 grid above it. "
    "Respond ONLY with a single integer from 1 to 6 representing the correct image index "
    "(1: Top-Left, 2: Top-Middle, 3: Top-Right, 4: Bottom-Left, 5: Bottom-Middle, 6: Bottom-Right). "
    "Do not explain, just output the number."
)
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
FALLBACK_GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-latest",
]


def load_api_key(explicit_api_key: Optional[str] = None) -> Optional[str]:
    """Resolve API key from explicit value, .env, then environment variables."""
    if explicit_api_key and str(explicit_api_key).strip():
        return str(explicit_api_key).strip()

    try:
        env_path = resource_path(".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
    except Exception:
        pass

    for key_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        key_val = os.getenv(key_name)
        if key_val and key_val.strip():
            return key_val.strip()

    return None


def _normalize_model_name(raw_name: str) -> str:
    name = str(raw_name or "").strip()
    if name.startswith("models/"):
        return name[len("models/") :]
    return name


def get_available_models(api_key: Optional[str] = None) -> List[str]:
    """
    Return available Gemini model names that can generate content for image+text prompts.
    """
    resolved_key = load_api_key(api_key)
    if not resolved_key:
        return [DEFAULT_GEMINI_MODEL]

    try:
        client = genai.Client(api_key=resolved_key)
        discovered: List[str] = []

        for model in client.models.list():
            model_name = _normalize_model_name(getattr(model, "name", ""))
            if not model_name:
                continue

            supported_methods = getattr(model, "supported_generation_methods", None)
            if supported_methods and "generateContent" not in supported_methods:
                continue

            if "gemini" not in model_name.lower():
                continue

            discovered.append(model_name)

        # Stable ordering for UI.
        discovered = sorted(set(discovered), key=lambda item: item.lower())

        if DEFAULT_GEMINI_MODEL in discovered:
            discovered.remove(DEFAULT_GEMINI_MODEL)
            discovered.insert(0, DEFAULT_GEMINI_MODEL)

        return discovered or [DEFAULT_GEMINI_MODEL]
    except Exception:
        return [DEFAULT_GEMINI_MODEL]


def _extract_answer_index(response_text: str) -> Optional[int]:
    """Extract the first integer in [1, 6] from model output."""
    if not response_text:
        return None

    text = response_text.strip()

    match = re.search(r"\b([1-6])\b", text)
    if match:
        return int(match.group(1))

    for char in text:
        if char in "123456":
            return int(char)

    return None


def _build_model_chain(preferred_model: str, api_key: str) -> List[str]:
    """Build ordered unique model candidates for fallback retries."""
    chain: List[str] = []

    def _add_model(raw_model: str) -> None:
        model = _normalize_model_name(raw_model)
        if not model:
            return
        if model not in chain:
            chain.append(model)

    _add_model(preferred_model or DEFAULT_GEMINI_MODEL)

    for available in get_available_models(api_key=api_key):
        _add_model(available)

    for fallback in FALLBACK_GEMINI_MODELS:
        _add_model(fallback)

    return chain


def _is_auth_error(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        token in text
        for token in (
            "401",
            "403",
            "permission denied",
            "invalid api key",
            "api key not valid",
            "unauthenticated",
            "forbidden",
        )
    )


def _is_model_temporarily_unavailable(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        token in text
        for token in (
            "503",
            "unavailable",
            "high demand",
            "resource_exhausted",
            "try again later",
            "temporarily",
            "overloaded",
        )
    )


def _is_model_not_supported(error: Exception) -> bool:
    text = str(error).lower()
    return any(
        token in text
        for token in (
            "404",
            "not found",
            "not supported",
            "unsupported",
            "unknown model",
        )
    )


def _sync_generate_answer(image_path: str, api_key: str, model_name: str) -> Optional[int]:
    if not os.path.exists(image_path):
        return None

    model = model_name.strip() if model_name and model_name.strip() else DEFAULT_GEMINI_MODEL
    model_chain = _build_model_chain(preferred_model=model, api_key=api_key)

    client = genai.Client(api_key=api_key)
    with Image.open(image_path) as image:
        last_error: Optional[Exception] = None

        for index, candidate_model in enumerate(model_chain):
            try:
                response = client.models.generate_content(
                    model=candidate_model,
                    contents=[_CAPTCHA_PROMPT, image.copy()],
                )

                text = getattr(response, "text", None)
                if not text:
                    if index < len(model_chain) - 1:
                        continue
                    return None

                answer = _extract_answer_index(text)
                if answer is not None:
                    if candidate_model != model:
                        print(
                            "[GeminiClient] Fallback model solved CAPTCHA: "
                            f"{candidate_model} (primary: {model})"
                        )
                    return answer

                if index < len(model_chain) - 1:
                    continue
                return None

            except Exception as error:
                last_error = error

                if _is_auth_error(error):
                    raise

                if index < len(model_chain) - 1 and (
                    _is_model_temporarily_unavailable(error)
                    or _is_model_not_supported(error)
                ):
                    print(
                        "[GeminiClient] Model failed, trying fallback: "
                        f"{candidate_model} -> {model_chain[index + 1]} | {error}"
                    )
                    continue

                if index < len(model_chain) - 1:
                    print(
                        "[GeminiClient] Unexpected model error, trying next fallback: "
                        f"{candidate_model} | {error}"
                    )
                    continue

                raise

        if last_error is not None:
            raise last_error

    return None


async def solve_captcha_with_gemini(
    image_path: str,
    api_key: Optional[str] = None,
    model_name: str = DEFAULT_GEMINI_MODEL,
) -> Optional[int]:
    """Solve CAPTCHA with selected Gemini model and return 1..6 index."""
    resolved_key = load_api_key(api_key)
    if not resolved_key:
        raise ValueError(
            "Gemini API Key not found. Configure captcha.api_key in UI or set GEMINI_API_KEY."
        )

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _sync_generate_answer,
        image_path,
        resolved_key,
        model_name,
    )


def get_solver():
    """Backward-compatible helper for legacy imports."""
    return type("Solver", (), {"solve_captcha_with_gemini": solve_captcha_with_gemini})()
