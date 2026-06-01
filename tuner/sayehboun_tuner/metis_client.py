import json
import os
import re
from typing import Any

import httpx
import metisai
from metisai.metistypes import MessageContent, MessageRequest

METIS_API_BASE = "https://api.metisai.ir/api/v1"


def use_system_proxy() -> bool:
    return os.getenv("USE_SYSTEM_PROXY", "").strip().lower() in {"1", "true", "yes"}


def metis_timeout() -> float:
    raw = os.getenv("METIS_TIMEOUT", "180").strip()
    try:
        return float(raw)
    except ValueError:
        return 180.0


def create_metis_bot(api_key: str, bot_id: str) -> metisai.MetisBot:
    return metisai.MetisBot(
        api_key=api_key,
        bot_id=bot_id,
        trust_env=use_system_proxy(),
    )


def fetch_bot_instructions(api_key: str, bot_id: str) -> str:
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{METIS_API_BASE}/bots/{bot_id}"
    with httpx.Client(trust_env=use_system_proxy(), timeout=30.0) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
    instructions = data.get("instructions") or ""
    return str(instructions).strip()


def save_bot_instructions(api_key: str, bot_id: str, instructions: str) -> None:
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{METIS_API_BASE}/bots/{bot_id}"
    with httpx.Client(trust_env=use_system_proxy(), timeout=60.0) as client:
        response = client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        data["instructions"] = instructions
        put_response = client.put(url, headers=headers, json=data)
        put_response.raise_for_status()


def send_evaluator_request(
    api_key: str,
    evaluator_bot_id: str,
    payload: dict[str, Any],
) -> str:
    bot = create_metis_bot(api_key, evaluator_bot_id)
    session = bot.create_session()
    try:
        user_text = (
            "ارزیابی session واقعی از DB. فقط JSON معتبر طبق schema برگردان.\n\n"
            + json.dumps(payload, ensure_ascii=False)
        )
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


def parse_json_from_llm(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Evaluator response did not contain JSON object")
    return json.loads(cleaned[start : end + 1])
