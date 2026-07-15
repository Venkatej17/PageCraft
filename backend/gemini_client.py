"""
Minimal direct-to-Gemini helper for PageCraft. No platform proxy, no billing —
just a plain REST call to Google's free-tier Gemini API using your own key.
"""

import os
import requests


def generate_html(prompt: str, system_instruction: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey "
            "and add it to your backend .env file."
        )
    model = os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system_instruction}]},
    }
    resp = requests.post(url, json=payload, timeout=240)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]
