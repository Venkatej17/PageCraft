"""
Minimal direct-to-Gemini helper for PageCraft. No platform proxy, no billing —
just a plain REST call to Google's free-tier Gemini API using your own key.
Includes retry-with-backoff and a fallback to a stable model, since preview
models occasionally return transient 503 "overloaded" errors.
"""

import os
import time
import requests

STABLE_FALLBACK_MODEL = "gemini-2.5-flash"


def _call(model: str, prompt: str, system_instruction: str, api_key: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
    }
    resp = requests.post(url, json=payload, timeout=240)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def generate_html(prompt: str, system_instruction: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey "
            "and add it to your backend .env file."
        )
    model = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

    # Try the configured model with a couple of quick retries for transient errors.
    last_error = None
    for attempt in range(3):
        try:
            return _call(model, prompt, system_instruction, api_key)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            last_error = e
            if status in (503, 429) and attempt < 2:
                time.sleep(2 * (attempt + 1))  # 2s, then 4s
                continue
            break
        except requests.exceptions.RequestException as e:
            last_error = e
            break

    # If the configured model is a preview model and kept failing, fall back to the stable one.
    if model != STABLE_FALLBACK_MODEL:
        try:
            return _call(STABLE_FALLBACK_MODEL, prompt, system_instruction, api_key)
        except Exception:
            pass  # fall through to raising the original error below

    raise last_error
