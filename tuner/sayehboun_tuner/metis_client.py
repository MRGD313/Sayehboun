import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx
import metisai
from metisai.metistypes import MessageContent, MessageRequest

_SAYEH_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_SAYEH_ROOT) not in sys.path:
    sys.path.insert(0, str(_SAYEH_ROOT))

from metis_utils import (  # noqa: E402
    apply_metis_direct_network,
    create_metis_bot as _create_metis_bot,
    metis_http_client_kwargs,
)

METIS_API_BASE = "https://api.metisai.ir/api/v1"


def metis_timeout() -> float:
    raw = os.getenv("METIS_TIMEOUT", "180").strip()
    try:
        return float(raw)
    except ValueError:
        return 180.0


def create_metis_bot(api_key: str, bot_id: str) -> metisai.MetisBot:
    return _create_metis_bot(api_key, bot_id)


def fetch_bot_instructions(api_key: str, bot_id: str) -> str:
    apply_metis_direct_network()
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{METIS_API_BASE}/bots/{bot_id}"
    with httpx.Client(timeout=30.0, **metis_http_client_kwargs()) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
    instructions = data.get("instructions") or ""
    return str(instructions).strip()


def save_bot_instructions(api_key: str, bot_id: str, instructions: str) -> None:
    apply_metis_direct_network()
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{METIS_API_BASE}/bots/{bot_id}"
    with httpx.Client(timeout=60.0, **metis_http_client_kwargs()) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        data["instructions"] = instructions
        put_response = client.put(url, headers=headers, json=data)
        put_response.raise_for_status()


def send_tuner_request(
    api_key: str,
    evaluator_bot_id: str,
    payload: dict[str, Any],
    *,
    intro: str,
) -> str:
    bot = create_metis_bot(api_key, evaluator_bot_id)
    session = bot.create_session()
    try:
        user_text = intro + json.dumps(payload, ensure_ascii=False)
        data = MessageRequest(
            message=MessageContent(type="USER", content=user_text),
        )
        response = bot.post(
            f"session/{session.id}/message",
            json=data.model_dump(),
            timeout=metis_timeout(),
        )
        response.raise_for_status()
        message = response.json()
        content = message.get("content") or ""
        return str(content).strip()
    finally:
        try:
            bot.delete_session(session)
        except Exception:
            pass


def extract_revised_prompt(text: str) -> str:
    """Pull the revised system prompt from a plain-text LLM reply."""
    cleaned = text.strip()
    fence = re.search(r"```(?:text|markdown)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    marker = "\n---RULE_CHANGE---"
    if marker in cleaned:
        cleaned = cleaned.split(marker, 1)[0].strip()
    return cleaned.strip()
