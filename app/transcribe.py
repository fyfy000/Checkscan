"""Send a cheque picture to an OpenAI-compatible vision model."""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

import httpx
from PIL import Image

SYSTEM = """You read pages from customer accounting PDFs. A picture may show one handwritten cheque, several cheques on the same photo, or an ordinary typed page.

Return a single JSON object and nothing else. Use this shape:
{
  "cheques": [
    {
      "is_cheque": true,
      "cheque_number": "",
      "date": "",
      "payee": "",
      "amount_numeric": "",
      "amount_words": "",
      "currency": "",
      "bank_name": "",
      "branch": "",
      "drawer": "",
      "memo": "",
      "signature_present": "unknown",
      "micr": "",
      "confidence": 0.0,
      "illegible": [],
      "typed_text": "",
      "notes": ""
    }
  ]
}

Rules:
- If the picture shows more than one cheque, put each cheque in the array as its own object. Do not merge them into one record.
- Order the array from the top of the picture to the bottom. When two cheques sit side by side, put the left one first.
- A single cheque is still an array of one object.
- If the page is a cheque, set is_cheque to true and leave typed_text empty.
- If the page is typed writing rather than a cheque, return one object with is_cheque false and that writing in typed_text.
- If the page is neither, return one object with is_cheque false and the other fields empty.
- Copy handwriting as plainly as you can. Do not invent digits, names, or dates.
- If a field is unclear, use an empty string and add the field name to illegible.
- Keep amount_words as the words written on the cheque, not a new amount you calculated.
- amount_numeric is the figure as written, including a currency symbol when one is written.
- signature_present is "yes", "no", or "unknown".
- confidence is your overall confidence from 0 to 1 for that cheque.
- notes is only for something an accountant should look at, such as a strikethrough. Otherwise use an empty string.
"""


def prepare_image(image_bytes: bytes) -> tuple[bytes, str]:
    image = Image.open(io.BytesIO(image_bytes))
    image = image.convert("RGB")
    image.thumbnail((2000, 2000))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue(), "image/jpeg"


def parse_model_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            raise RuntimeError("The model did not return a cheque record.")
        parsed = json.loads(match.group(0))
    if isinstance(parsed, list):
        return {"cheques": [item for item in parsed if isinstance(item, dict)]}
    if not isinstance(parsed, dict):
        raise RuntimeError("The model did not return a cheque record.")
    return parsed


def _endpoint(settings: dict[str, str]) -> str:
    base = settings.get("base_url", "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise RuntimeError("The model address must start with http:// or https://.")
    return base + "/chat/completions"


def chat(settings: dict[str, str], messages: list[dict], json_mode: bool = False, timeout: float = 90) -> dict:
    key = settings.get("api_key", "").strip()
    model = settings.get("model", "").strip()
    if not key:
        raise RuntimeError("Add an API key in Settings before reading handwriting.")
    if not model:
        raise RuntimeError("Add a model name in Settings.")
    payload: dict[str, Any] = {"model": model, "temperature": 0, "messages": messages}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        response = httpx.post(_endpoint(settings), json=payload, headers=headers, timeout=timeout)
        if response.status_code == 400 and json_mode:
            payload.pop("response_format", None)
            response = httpx.post(_endpoint(settings), json=payload, headers=headers, timeout=timeout)
        if response.status_code >= 400:
            detail = response.text[:200].strip()
            raise RuntimeError(f"The model service returned {response.status_code}. {detail}")
        body = response.json()
    except httpx.TimeoutException as exc:
        raise RuntimeError("The model took too long to answer.") from exc
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not reach the model service. Check the address and your connection.") from exc
    try:
        return body
    except Exception as exc:
        raise RuntimeError("The model service sent an unreadable answer.") from exc


def message_text(body: dict) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("The model service sent an unreadable answer.") from exc
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("The model returned an empty answer.")
    return content


def transcribe_image(image_bytes: bytes, settings: dict[str, str], hint: str = "") -> dict[str, Any]:
    jpeg, mime = prepare_image(image_bytes)
    encoded = base64.standard_b64encode(jpeg).decode("ascii")
    instruction = "Read this page for an accounting file."
    if hint:
        instruction += "\n\nA text layer on the page produced the following. It may be wrong. Trust the image.\n" + hint[:2000]
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        },
    ]
    body = chat(settings, messages, json_mode=True, timeout=120)
    return parse_model_json(message_text(body))


def ping_model(settings: dict[str, str]) -> str:
    messages = [
        {"role": "system", "content": "Reply with the single word ok."},
        {"role": "user", "content": "Say ok."},
    ]
    body = chat(settings, messages, json_mode=False, timeout=30)
    answer = message_text(body).strip()
    return answer[:80]
