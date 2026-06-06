import os
import time
import traceback
from typing import Any

import httpx
import metisai
from metisai.metistypes import Message, MessageContent, MessageRequest, Session

METIS_REQUEST_TIMEOUT = 120.0
METIS_MAX_ATTEMPTS = 3
METIS_API_BASE = "https://api.metisai.ir"
METIS_API_HOSTS = ("api.metisai.ir", "metisai.ir", ".metisai.ir")

_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def use_system_proxy() -> bool:
    """When true, Bale HTTP may use OS/VPN proxy. Metis always stays direct."""
    return os.getenv("USE_SYSTEM_PROXY", "").strip().lower() in {"1", "true", "yes"}


def metis_uses_direct_connection() -> bool:
    """Metis bypasses VPN/proxy unless METIS_USE_SYSTEM_PROXY=1."""
    return os.getenv("METIS_USE_SYSTEM_PROXY", "").strip().lower() not in {
        "1",
        "true",
        "yes",
    }


def apply_metis_direct_network() -> None:
    """
    Route Metis API calls outside VPN/proxy (LAN/proxy VPN modes on Windows).
    Safe to call more than once. Does not change Bale polling unless USE_SYSTEM_PROXY=1.
    """
    if not metis_uses_direct_connection():
        metis_log("network", "metis_proxy_mode", mode="system_proxy")
        return

    for key in _PROXY_ENV_KEYS:
        os.environ.pop(key, None)

    existing = os.environ.get("NO_PROXY", "") or os.environ.get("no_proxy", "")
    parts = [p.strip() for p in existing.split(",") if p.strip()]
    for host in METIS_API_HOSTS:
        if host not in parts:
            parts.append(host)
    no_proxy = ",".join(parts)
    os.environ["NO_PROXY"] = no_proxy
    os.environ["no_proxy"] = no_proxy
    metis_log("network", "metis_direct", no_proxy=no_proxy)


def metis_http_client_kwargs() -> dict[str, Any]:
    """httpx kwargs for Metis: direct by default (ignore VPN/proxy env)."""
    if metis_uses_direct_connection():
        return {"trust_env": False, "proxy": None}
    return {"trust_env": True}


def metis_log(service: str, step: str, **fields: object) -> None:
    parts = [f"[METIS:{service}] {step}"]
    for key, value in fields.items():
        parts.append(f"{key}={value}")
    line = " ".join(parts)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="backslashreplace").decode("ascii"), flush=True)


def classify_metis_error(err: Exception) -> str:
    if isinstance(err, httpx.ConnectTimeout):
        return "YOUR_NETWORK: connect timeout (proxy/VPN/firewall/internet)"
    if isinstance(err, httpx.ReadTimeout):
        return "YOUR_NETWORK or METIS_SLOW: read timeout"
    if isinstance(err, httpx.ProxyError):
        return "YOUR_NETWORK: proxy error (disable VPN/proxy or set USE_SYSTEM_PROXY=0)"
    if isinstance(err, httpx.HTTPStatusError):
        code = err.response.status_code
        if code == 504:
            return "METIS_SERVER: 504 gateway timeout (Metis overloaded/slow)"
        if code == 502:
            return "METIS_SERVER: 502 bad gateway"
        if code == 503:
            return "METIS_SERVER: 503 service unavailable"
        if code == 402:
            return "METIS_ACCOUNT: 402 insufficient balance"
        if code == 401:
            return "METIS_ACCOUNT: 401 invalid API key"
        if code >= 500:
            return f"METIS_SERVER: HTTP {code}"
        return f"METIS_API: HTTP {code}"
    message = str(err).lower()
    if "proxy" in message:
        return "YOUR_NETWORK: proxy-related error"
    if "ssl" in message or "handshake" in message:
        return "YOUR_NETWORK: SSL/TLS handshake failed"
    return f"OTHER: {type(err).__name__}"


def check_metis_network() -> None:
    apply_metis_direct_network()
    metis_log(
        "network",
        "startup_check_begin",
        metis_direct=metis_uses_direct_connection(),
        bale_use_system_proxy=use_system_proxy(),
    )
    modes: list[tuple[str, bool]] = [("direct", False)]
    if not metis_uses_direct_connection():
        modes.append(("system_proxy", True))

    for mode_name, trust_env in modes:
        t0 = time.time()
        try:
            with httpx.Client(trust_env=trust_env, timeout=15.0) as client:
                response = client.get(METIS_API_BASE)
            elapsed = round(time.time() - t0, 2)
            metis_log(
                "network",
                "reachable",
                mode=mode_name,
                status=response.status_code,
                elapsed_s=elapsed,
            )
        except Exception as err:
            elapsed = round(time.time() - t0, 2)
            metis_log(
                "network",
                "unreachable",
                mode=mode_name,
                elapsed_s=elapsed,
                diagnosis=classify_metis_error(err),
                detail=str(err)[:200],
            )


def create_metis_bot(api_key: str, bot_id: str) -> metisai.MetisBot:
    apply_metis_direct_network()
    kwargs = metis_http_client_kwargs()
    metis_log(
        "client",
        "init",
        bot_id=bot_id,
        metis_direct=metis_uses_direct_connection(),
        trust_env=kwargs.get("trust_env"),
    )
    return metisai.MetisBot(api_key=api_key, bot_id=bot_id, **kwargs)


def _session_id(session: Session | str) -> str:
    if isinstance(session, str):
        return session
    return str(session.id)


def send_message_timed(
    bot: Any,
    session: Session | str,
    prompt: str,
    *,
    service: str,
) -> Message:
    data = MessageRequest(
        message=MessageContent(
            type="USER",
            content=prompt,
        )
    )
    sid = _session_id(session)
    metis_log(
        service,
        "send_message_start",
        session_id=sid,
        prompt_chars=len(prompt),
        timeout_s=METIS_REQUEST_TIMEOUT,
    )
    t0 = time.time()
    response = bot.post(
        f"session/{sid}/message",
        json=data.model_dump(),
        timeout=METIS_REQUEST_TIMEOUT,
    )
    elapsed = round(time.time() - t0, 2)
    response.raise_for_status()
    message = Message(**response.json())
    content_len = len((getattr(message, "content", "") or ""))
    metis_log(
        service,
        "send_message_ok",
        session_id=sid,
        elapsed_s=elapsed,
        response_chars=content_len,
    )
    return message


def send_message_with_retry(
    bot: Any,
    prompt: str,
    *,
    get_session,
    reset_session,
    service: str = "triage",
    max_attempts: int | None = None,
) -> str:
    attempts = METIS_MAX_ATTEMPTS if max_attempts is None else max(1, max_attempts)
    metis_log(service, "request_begin", prompt_chars=len(prompt), max_attempts=attempts)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        metis_log(service, "attempt", number=attempt, of=attempts)
        try:
            t_session = time.time()
            session = get_session()
            metis_log(
                service,
                "session_ready",
                session_id=_session_id(session),
                elapsed_s=round(time.time() - t_session, 2),
            )
            message = send_message_timed(bot, session, prompt, service=service)
            content = getattr(message, "content", "") or ""
            text = str(content).strip()
            if text:
                metis_log(service, "request_success", response_chars=len(text))
                return text
            last_error = RuntimeError("Metis returned empty content")
            metis_log(service, "empty_response")
        except (httpx.HTTPError, RuntimeError) as err:
            last_error = err
            metis_log(
                service,
                "attempt_failed",
                number=attempt,
                diagnosis=classify_metis_error(err),
                error_type=type(err).__name__,
                detail=str(err)[:300],
            )
            reset_session()
            if attempt < attempts:
                metis_log(service, "retry_wait", seconds=2)
                time.sleep(2)
    if last_error:
        metis_log(
            service,
            "request_failed_all_attempts",
            diagnosis=classify_metis_error(last_error),
        )
        traceback.print_exc()
        raise last_error
    return ""
