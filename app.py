import json
import os
import time
import traceback
from pathlib import Path

import msvcrt
import requests
from dotenv import load_dotenv

from db import Database
from deepseek_client import DeepSeekClient
from history_formatter import MetisHistoryFormatterClient
from metis_utils import check_metis_network, metis_log

START_MESSAGE = "سلام...در خدمتم...مشکل سلامتی اتون رو بفرمایید"
FINAL_MESSAGE = "اطلاعات شما ثبت شد. تا دقایقی دیگه نتیجه برای شما ارسال می شود."
DOCTOR_FORMATTER_ERROR = "error in history formatter bot"
PHASE3_ENDING_MESSAGE = "لطفاً به این موارد پاسخ دهید تا اطلاعات شما برای بررسی نهایی آماده شود."
DEEPSEEK_ERROR_MESSAGE = "شرمنده ... ظاهرا مشکلی پیش آمده است..لطفا کمی بعد دوباره تلاش کنید"
AI_WAIT_MESSAGE = "در حال آماده‌سازی سوالات... لطفاً چند لحظه صبر کنید."
CONTINUE_BUTTON_TEXT = "ادامه بده"
CONTINUE_HINT_TEXT = 'وقتی پاسختون تکمیل شد روی دکمه "ادامه بده" بزنید'
EMPTY_CONTINUE_WARNING = "لطفاً اول پاسخ‌هاتون رو ارسال کنید، بعد روی دکمه ادامه بده بزنید."
DEMOGRAPHIC_QUESTIONS: list[tuple[str, str]] = [
    ("age", "لطفاً سن‌تان را بفرمایید."),
    ("gender", "لطفاً جنسیت‌تان را بفرمایید."),
    ("job", "لطفاً شغل‌تان را بفرمایید."),
    ("medications", "لطفاً داروهای مصرفی فعلی‌تان را بفرمایید."),
    ("medical_docs", "لطفاً اگر مدارک پزشکی دارید، خلاصه‌اش را بفرمایید (اگر ندارید بنویسید ندارم)."),
    ("history", "لطفاً بیماری زمینه‌ای یا سابقه جراحی را بفرمایید (اگر ندارید بنویسید ندارم)."),
]
LOCK_PATH = Path(".bot.instance.lock")

# Bypass broken Windows/system proxy for Bale + Metis (set USE_SYSTEM_PROXY=1 to enable).
_http = requests.Session()
_http.trust_env = os.getenv("USE_SYSTEM_PROXY", "").strip().lower() in {"1", "true", "yes"}

def _get_or_create_latest_session_id(db: Database, chat_id: str) -> int:
    latest = db.get_latest_session(chat_id)
    if latest:
        return latest.id
    return db.create_session(chat_id)


def _next_missing_demographic(demographics: dict[str, str]) -> tuple[str, str] | None:
    for key, question in DEMOGRAPHIC_QUESTIONS:
        value = (demographics.get(key) or "").strip()
        if not value:
            return key, question
    return None


def _last_bot_message_text(session_messages: list[dict[str, str]]) -> str:
    for item in reversed(session_messages):
        if item.get("role") == "bot":
            return (item.get("text") or "").strip()
    return ""


def _is_phase3_batch(bot_text: str) -> bool:
    return PHASE3_ENDING_MESSAGE in bot_text


def _maybe_register_doctor(db: Database, message: dict) -> None:
    sender = message.get("from") or message.get("from_user") or {}
    username = (sender.get("username") or "").strip().lower().lstrip("@")
    doctor_username = (
        os.getenv("DOCTOR_BALE_USERNAME", "").strip().lower().lstrip("@")
    )
    if username and username == doctor_username:
        doctor_chat_id = str(sender.get("id") or "").strip()
        if doctor_chat_id:
            db.set_doctor_chat_id(doctor_chat_id)


def _resolve_doctor_chat_id(db: Database) -> str:
    doctor_chat_id = db.get_doctor_chat_id().strip()
    if doctor_chat_id:
        return doctor_chat_id
    return os.getenv("DOCTOR_CHAT_ID", "").strip()


def _build_formatter_payload(
    *,
    patient_chat_id: str,
    session_id: int,
    chief_complaint: str,
    demographics: dict[str, str],
    messages: list[dict[str, str]],
) -> str:
    demo_lines = [f"{key}: {value}" for key, value in demographics.items() if not key.startswith("_")]
    message_lines = [
        f"[{item.get('role', 'unknown')}] {item.get('text', '')}" for item in messages
    ]
    return (
        f"patient_chat_id: {patient_chat_id}\n"
        f"session_id: {session_id}\n"
        f"chief_complaint: {chief_complaint}\n\n"
        "demographics:\n"
        + "\n".join(demo_lines)
        + "\n\nsession_messages:\n"
        + "\n".join(message_lines)
    )


def _format_history_with_retry(formatter: MetisHistoryFormatterClient, payload: str) -> str:
    last_error: Exception | None = None
    for _ in range(2):
        try:
            result = formatter.format_history(payload)
            if result.strip():
                return result.strip()
        except Exception as err:
            last_error = err
        time.sleep(1)
    if last_error:
        raise last_error
    return ""


def _send_doctor_report(
    *,
    token: str,
    doctor_chat_id: str,
    patient_chat_id: str,
    session_id: int,
    session_time: str,
    body: str,
) -> None:
    header = (
        f"pid: {patient_chat_id}\n"
        f"sid: {session_id}\n"
        f"time: {session_time}\n\n"
        f"{body}"
    )
    _send_text(token, doctor_chat_id, header)


def _complete_session_and_notify_doctor(
    db: Database,
    formatter: MetisHistoryFormatterClient,
    *,
    token: str,
    patient_chat_id: str,
    session_id: int,
    demographics: dict[str, str],
) -> None:
    _send_text(token, patient_chat_id, FINAL_MESSAGE)
    db.append_message(session_id, "bot", FINAL_MESSAGE)

    session = db.get_session_by_id(session_id)
    if not session:
        return

    doctor_chat_id = _resolve_doctor_chat_id(db)
    payload = _build_formatter_payload(
        patient_chat_id=patient_chat_id,
        session_id=session_id,
        chief_complaint=session.chief_complaint,
        demographics=demographics,
        messages=session.messages,
    )

    try:
        structured = _format_history_with_retry(formatter, payload)
    except Exception:
        if doctor_chat_id:
            _send_doctor_report(
                token=token,
                doctor_chat_id=doctor_chat_id,
                patient_chat_id=patient_chat_id,
                session_id=session_id,
                session_time=session.created_at,
                body=DOCTOR_FORMATTER_ERROR,
            )
        return

    if not structured:
        if doctor_chat_id:
            _send_doctor_report(
                token=token,
                doctor_chat_id=doctor_chat_id,
                patient_chat_id=patient_chat_id,
                session_id=session_id,
                session_time=session.created_at,
                body=DOCTOR_FORMATTER_ERROR,
            )
        return

    db.append_message(session_id, "formatter", structured)
    if doctor_chat_id:
        _send_doctor_report(
            token=token,
            doctor_chat_id=doctor_chat_id,
            patient_chat_id=patient_chat_id,
            session_id=session_id,
            session_time=session.created_at,
            body=structured,
        )


def _send_phase_questions(
    db: Database,
    deepseek: DeepSeekClient,
    *,
    token: str,
    chat_id: str,
    session_id: int,
    phase: int,
    chief_complaint: str,
    demographics: dict[str, str],
    session_messages: list[dict[str, str]],
) -> None:
    metis_log(
        "bot",
        "phase_questions_start",
        chat_id=chat_id,
        session_id=session_id,
        phase=phase,
        cc_chars=len(chief_complaint),
    )
    _send_text(token, chat_id, AI_WAIT_MESSAGE)
    db.append_message(session_id, "bot", AI_WAIT_MESSAGE)
    questions_text = deepseek.generate_phase_questions(
        chat_key=chat_id,
        current_phase=phase,
        chief_complaint=chief_complaint,
        demographics=demographics,
        session_messages=session_messages,
    )
    if not questions_text:
        metis_log("bot", "phase_questions_empty_response", session_id=session_id, phase=phase)
        return
    metis_log("bot", "phase_questions_ok", session_id=session_id, phase=phase, chars=len(questions_text))
    final_text = f"{questions_text}\n\n{CONTINUE_HINT_TEXT}"
    _send_text(
        token,
        chat_id,
        final_text,
        reply_markup=_continue_keyboard(),
    )
    db.append_message(session_id, "bot", final_text)
    db.update_session(
        session_id,
        current_phase=phase,
        waiting_for_continue=1,
        answer_buffer=[],
    )


def _send_text(token: str, chat_id: str, text: str, reply_markup: dict | None = None) -> None:
    payload: dict[str, object] = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    response = _http.post(
        f"https://tapi.bale.ai/bot{token}/sendMessage",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"sendMessage failed: {data}")


def _continue_keyboard() -> dict:
    return {
        "keyboard": [[{"text": CONTINUE_BUTTON_TEXT}]],
        "resize_keyboard": True,
    }


def _acquire_single_instance_lock():
    """
    Windows-safe single instance lock.
    Keeps a file handle open for process lifetime.
    """
    lock_file = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_file.close()
        raise RuntimeError("Bot is already running in another process.")
    lock_file.seek(0)
    lock_file.truncate(0)
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _process_text_message(
    db: Database,
    deepseek: DeepSeekClient,
    formatter: MetisHistoryFormatterClient,
    *,
    token: str,
    chat_id: str,
    text: str,
    raw_message: dict | None = None,
) -> None:
    if raw_message:
        _maybe_register_doctor(db, raw_message)
    if text == "/start":
        deepseek.reset_session(chat_id)
        session_id = db.create_session(chat_id)
        db.append_message(session_id, "user", text)
        start_text = f"{START_MESSAGE}\n\n{CONTINUE_HINT_TEXT}"
        _send_text(
            token,
            chat_id,
            start_text,
            reply_markup=_continue_keyboard(),
        )
        db.append_message(session_id, "bot", start_text)
        db.update_session(
            session_id,
            waiting_for_continue=1,
            answer_buffer=[],
        )
        return

    latest_session_id = _get_or_create_latest_session_id(db, chat_id)
    db.append_message(latest_session_id, "user", text)
    session = db.get_latest_session(chat_id)
    if not session:
        return

    if session.waiting_for_continue == 1:
        if text == CONTINUE_BUTTON_TEXT:
            if not session.answer_buffer:
                _send_text(
                    token,
                    chat_id,
                    EMPTY_CONTINUE_WARNING,
                    reply_markup=_continue_keyboard(),
                )
                db.append_message(session.id, "bot", EMPTY_CONTINUE_WARNING)
                return

            last_bot_before_submit = _last_bot_message_text(session.messages)
            combined_answer = "\n".join(session.answer_buffer).strip()
            db.append_message(session.id, "user", combined_answer)
            db.update_session(
                session.id,
                waiting_for_continue=0,
                answer_buffer=[],
            )
            session = db.get_latest_session(chat_id)
            if not session:
                return

            if _is_phase3_batch(last_bot_before_submit):
                demographics = db.get_demographics(chat_id)
                _complete_session_and_notify_doctor(
                    db,
                    formatter,
                    token=token,
                    patient_chat_id=chat_id,
                    session_id=session.id,
                    demographics=demographics,
                )
                return

            text = combined_answer
        else:
            new_buffer = [*session.answer_buffer, text]
            db.update_session(session.id, answer_buffer=new_buffer)
            return

    demographics = db.get_demographics(chat_id)

    if session.pending_field:
        demographics[session.pending_field] = text
        db.upsert_demographics(chat_id, demographics)
        db.update_session(session.id, pending_field="")
        session = db.get_latest_session(chat_id)
        if not session:
            return

    if not session.chief_complaint:
        db.update_session(session.id, chief_complaint=text)
        session = db.get_latest_session(chat_id)
        if not session:
            return

    demographics = db.get_demographics(chat_id)
    missing = _next_missing_demographic(demographics)
    if missing:
        pending_key, question = missing
        db.update_session(session.id, pending_field=pending_key)
        _send_text(token, chat_id, question)
        db.append_message(session.id, "bot", question)
        return

    try:
        # First AI call after CC + demographics: phase 1.
        # After each phase batch is answered: advance 1 -> 2 -> 3 only.
        if session.chief_complaint and session.current_phase >= 1:
            last_bot = _last_bot_message_text(session.messages)
            if "🔶" in last_bot:
                next_phase = session.current_phase + 1
                if next_phase > 3:
                    _complete_session_and_notify_doctor(
                        db,
                        formatter,
                        token=token,
                        patient_chat_id=chat_id,
                        session_id=session.id,
                        demographics=demographics,
                    )
                    return
                _send_phase_questions(
                    db,
                    deepseek,
                    token=token,
                    chat_id=chat_id,
                    session_id=session.id,
                    phase=next_phase,
                    chief_complaint=session.chief_complaint,
                    demographics=demographics,
                    session_messages=session.messages,
                )
                return

        _send_phase_questions(
            db,
            deepseek,
            token=token,
            chat_id=chat_id,
            session_id=session.id,
            phase=1,
            chief_complaint=session.chief_complaint,
            demographics=demographics,
            session_messages=session.messages,
        )
    except Exception as err:
        metis_log("bot", "phase_questions_error", chat_id=chat_id, error=str(err)[:300])
        traceback.print_exc()
        _send_text(token, chat_id, DEEPSEEK_ERROR_MESSAGE)
        db.append_message(session.id, "bot", DEEPSEEK_ERROR_MESSAGE)


def main() -> None:
    lock_handle = _acquire_single_instance_lock()
    load_dotenv()
    token = os.getenv("BALE_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BALE_BOT_TOKEN is missing in environment.")
    # MetisAI API key (wrapper for DeepSeek)
    deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is missing in environment.")
    metis_bot_id = os.getenv("METIS_BOT_ID", "").strip()
    if not metis_bot_id:
        raise RuntimeError("METIS_BOT_ID is missing in environment.")
    metis_bot_id_backup = os.getenv("METIS_BOT_ID_BACKUP", "").strip()
    metis_structure_bot_id = os.getenv("METIS_STRUCTURE_BOT_ID", "").strip()
    if not metis_structure_bot_id:
        raise RuntimeError("METIS_STRUCTURE_BOT_ID is missing in environment.")
    # deepseek-chat is the recommended model name via MetisAI DeepSeek endpoint
    deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
    if not deepseek_model:
        deepseek_model = "deepseek-chat"

    db_path = os.getenv("SQLITE_DB_PATH", "bot.db")
    db = Database(db_path)
    db.init()
    deepseek = DeepSeekClient(
        api_key=deepseek_api_key,
        model=deepseek_model,
        bot_id=metis_bot_id,
        backup_bot_id=metis_bot_id_backup or None,
    )
    if metis_bot_id_backup:
        print(f"Triage backup Metis bot configured: {metis_bot_id_backup}", flush=True)
    formatter = MetisHistoryFormatterClient(
        api_key=deepseek_api_key,
        bot_id=metis_structure_bot_id,
    )

    doctor_chat_id = _resolve_doctor_chat_id(db)
    if not doctor_chat_id:
        fallback_doctor_chat_id = os.getenv("DOCTOR_CHAT_ID", "").strip()
        if fallback_doctor_chat_id:
            db.set_doctor_chat_id(fallback_doctor_chat_id)
            doctor_chat_id = fallback_doctor_chat_id
    if doctor_chat_id:
        print(f"Doctor chat_id resolved: {doctor_chat_id}", flush=True)
    else:
        print("Doctor chat_id not resolved yet.", flush=True)

    # Ensure long polling can receive updates.
    try:
        webhook_response = _http.post(
            f"https://tapi.bale.ai/bot{token}/deleteWebhook",
            timeout=20,
        )
        webhook_response.raise_for_status()
        webhook_data = webhook_response.json()
        print(f"deleteWebhook response: {webhook_data}", flush=True)
    except Exception as err:
        print(f"Webhook clear error: {err}", flush=True)

    prompt_version = os.getenv("PROMPT_VERSION", "v1").strip()
    manifest_path = Path(__file__).resolve().parent / "tuner" / "prompts" / "versions" / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            prompt_version = str(manifest.get("current") or prompt_version)
        except Exception:
            pass
    print(f"History taker prompt version: {prompt_version}", flush=True)

    check_metis_network()
    print("Bale bot is running (HTTP long polling).", flush=True)
    offset: int | None = None

    try:
        while True:
            try:
                payload: dict[str, int] = {"timeout": 30}
                if offset is not None:
                    payload["offset"] = offset
                response = _http.post(
                    f"https://tapi.bale.ai/bot{token}/getUpdates",
                    json=payload,
                    timeout=40,
                )
                response.raise_for_status()
                data = response.json()
                if not data.get("ok"):
                    time.sleep(1)
                    continue

                for update in data.get("result", []):
                    update_id = int(update.get("update_id", 0))
                    offset = update_id + 1

                    message = update.get("message") or {}
                    text = (message.get("text") or "").strip()
                    chat = message.get("chat") or {}
                    chat_id = str(chat.get("id") or chat.get("chat_id") or "")
                    if not chat_id:
                        sender = message.get("from") or message.get("from_user") or {}
                        chat_id = str(
                            sender.get("id")
                            or sender.get("chat_id")
                            or message.get("chat_id")
                            or ""
                        )
                    if not text or not chat_id:
                        continue
                    incoming = f"[BOT] incoming chat_id={chat_id} text_len={len(text)}"
                    try:
                        print(incoming, flush=True)
                    except UnicodeEncodeError:
                        print(incoming.encode("ascii", errors="backslashreplace").decode("ascii"), flush=True)
                    _process_text_message(
                        db,
                        deepseek,
                        formatter,
                        token=token,
                        chat_id=chat_id,
                        text=text,
                        raw_message=message,
                    )
            except Exception as err:
                print(f"Polling error: {err}", flush=True)
                time.sleep(2)
    finally:
        lock_handle.close()


if __name__ == "__main__":
    main()
