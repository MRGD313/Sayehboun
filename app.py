import json
import os
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import msvcrt
import requests
from dotenv import load_dotenv

from db import Database, Followup, JudgmentReview
from deepseek_client import DeepSeekClient
from doctor_profile import format_judgment_for_patient, resolve_reviewing_doctor, strip_patient_approval_header
from followup import (
    CLOSING_ESCALATION_INTRO,
    FOLLOWUP_USE_BUTTONS,
    build_intro,
    build_redflag_question,
    build_closing_message,
    build_followup_ai_context,
    compute_due_at,
    doctor_schedule_message,
    DOCTOR_FOLLOWUP_ESCALATION_BANNER,
    format_followup_context_text,
    session_has_followup,
    followup_poll_interval_sec,
    is_followup_test_mode,
    needs_doctor_visit,
    parse_redflag_callback,
    parse_trend_callback,
    parse_urgency_class,
    redflag_keyboard,
    should_escalate,
    trend_keyboard,
    trend_label,
)
from history_formatter import MetisHistoryFormatterClient
from judging_client import MetisJudgingClient
from metis_utils import apply_metis_direct_network, check_metis_network, metis_log

START_MESSAGE = "سلام...در خدمتم...مشکل سلامتی اتون رو بفرمایید"
FINAL_MESSAGE = "اطلاعات شما ثبت شد. تا دقایقی دیگه نتیجه برای شما ارسال می شود."
DOCTOR_FORMATTER_ERROR = "error in history formatter bot"
DOCTOR_JUDGING_ERROR = "error in judging bot"
DOCTOR_EDIT_PROMPT = "متن نهایی را برای بیمار بنویسید و ارسال کنید (بدون pid/sid/time)."
DOCTOR_SENT_TO_PATIENT_ACK = "✅ برای بیمار ارسال شد."
DOCTOR_ALREADY_SENT_ACK = "این نظر قبلاً برای بیمار ارسال شده است."
DOCTOR_APPROVE_BUTTON = "👍🏻"
DOCTOR_EDIT_BUTTON = "✏️ ویرایش"
DOCTOR_REVIEW_HINT = "👍🏻 = ارسال به بیمار | ✏️ ویرایش = ویرایش و ارسال"
JUDGMENT_APPROVE_CALLBACK_PREFIX = "judgment:approve:"
JUDGMENT_EDIT_CALLBACK_PREFIX = "judgment:edit:"
PHASE3_ENDING_MESSAGE = "لطفاً به این موارد پاسخ دهید تا اطلاعات شما برای بررسی نهایی آماده شود."
DEEPSEEK_ERROR_MESSAGE = "شرمنده ... ظاهرا مشکلی پیش آمده است..لطفا کمی بعد دوباره تلاش کنید"
AI_WAIT_MESSAGE = "در حال آماده‌سازی سوالات... لطفاً چند لحظه صبر کنید."
CONTINUE_BUTTON_TEXT = "ادامه بده"
RESTART_BUTTON_TEXT = "شروع مجدد"
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
            doctor = db.get_doctor_by_bale_chat_id(doctor_chat_id)
            if not doctor:
                active = db.get_active_doctor()
                if active and not active.bale_chat_id:
                    db.link_doctor_bale_chat_id(active.id, doctor_chat_id)


def _resolve_doctor_chat_id(db: Database) -> str:
    doctor_chat_id = db.get_doctor_chat_id().strip()
    if doctor_chat_id:
        return doctor_chat_id
    return os.getenv("DOCTOR_CHAT_ID", "").strip()


def _build_session_payload(
    *,
    patient_chat_id: str,
    session_id: int,
    chief_complaint: str,
    demographics: dict[str, str],
    messages: list[dict[str, str]],
    followup_context: dict | None = None,
) -> str:
    demo_lines = [f"{key}: {value}" for key, value in demographics.items() if not key.startswith("_")]
    message_lines = [
        f"[{item.get('role', 'unknown')}] {item.get('text', '')}" for item in messages
    ]
    parts = [
        f"patient_chat_id: {patient_chat_id}",
        f"session_id: {session_id}",
        f"chief_complaint: {chief_complaint}",
        "",
        "demographics:",
        "\n".join(demo_lines),
    ]
    if followup_context:
        parts.extend(["", format_followup_context_text(followup_context), ""])
    parts.extend(["session_messages:", "\n".join(message_lines)])
    return "\n".join(parts)


def _followup_context_for_session(db: Database, session_id: int, messages: list[dict]) -> dict | None:
    if not session_has_followup(messages):
        return None
    row = db.get_followup(session_id)
    return build_followup_ai_context(
        messages,
        urgency_class=row.urgency_class if row else "",
        trend=row.trend if row else "",
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


def _judge_with_retry(judging: MetisJudgingClient, payload: str) -> str:
    last_error: Exception | None = None
    for _ in range(2):
        try:
            result = judging.judge(payload)
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
    followup_escalation: bool = False,
) -> None:
    banner = ""
    if followup_escalation:
        banner = f"{DOCTOR_FOLLOWUP_ESCALATION_BANNER}\n\n"
    header = (
        f"pid: {patient_chat_id}\n"
        f"sid: {session_id}\n"
        f"time: {session_time}\n\n"
        f"{banner}"
        f"{body}"
    )
    _send_text(token, doctor_chat_id, header)


def _complete_session_and_notify_doctor(
    db: Database,
    formatter: MetisHistoryFormatterClient,
    judging: MetisJudgingClient,
    *,
    token: str,
    patient_chat_id: str,
    session_id: int,
    demographics: dict[str, str],
) -> None:
    _send_text(token, patient_chat_id, FINAL_MESSAGE, reply_markup=_chat_keyboard())
    db.append_message(session_id, "bot", FINAL_MESSAGE)

    session = db.get_session_by_id(session_id)
    if not session:
        return

    doctor_chat_id = _resolve_doctor_chat_id(db)
    followup_escalation = session_has_followup(session.messages)
    followup_context = _followup_context_for_session(db, session_id, session.messages)
    payload = _build_session_payload(
        patient_chat_id=patient_chat_id,
        session_id=session_id,
        chief_complaint=session.chief_complaint,
        demographics=demographics,
        messages=session.messages,
        followup_context=followup_context,
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
                followup_escalation=followup_escalation,
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
                followup_escalation=followup_escalation,
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
            followup_escalation=followup_escalation,
        )

    try:
        judgment = _judge_with_retry(judging, payload)
    except Exception:
        metis_log("bot", "judging_error", session_id=session_id)
        if doctor_chat_id:
            _send_doctor_report(
                token=token,
                doctor_chat_id=doctor_chat_id,
                patient_chat_id=patient_chat_id,
                session_id=session_id,
                session_time=session.created_at,
                body=DOCTOR_JUDGING_ERROR,
                followup_escalation=followup_escalation,
            )
        return

    if not judgment:
        if doctor_chat_id:
            _send_doctor_report(
                token=token,
                doctor_chat_id=doctor_chat_id,
                patient_chat_id=patient_chat_id,
                session_id=session_id,
                session_time=session.created_at,
                body=DOCTOR_JUDGING_ERROR,
                followup_escalation=followup_escalation,
            )
        return

    db.append_message(session_id, "judgment", judgment)
    if doctor_chat_id:
        _send_doctor_judgment_for_review(
            db,
            token=token,
            doctor_chat_id=doctor_chat_id,
            patient_chat_id=patient_chat_id,
            session_id=session_id,
            session_time=session.created_at,
            judgment=judgment,
            followup_escalation=followup_escalation,
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
    followup_context: dict | None = None,
) -> None:
    metis_log(
        "bot",
        "phase_questions_start",
        chat_id=chat_id,
        session_id=session_id,
        phase=phase,
        cc_chars=len(chief_complaint),
    )
    _send_text(token, chat_id, AI_WAIT_MESSAGE, reply_markup=_chat_keyboard())
    db.append_message(session_id, "bot", AI_WAIT_MESSAGE)
    questions_text = deepseek.generate_phase_questions(
        chat_key=chat_id,
        current_phase=phase,
        chief_complaint=chief_complaint,
        demographics=demographics,
        session_messages=session_messages,
        followup_context=followup_context,
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
        reply_markup=_chat_keyboard(),
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


def _answer_callback_query(token: str, callback_query_id: str, text: str = "") -> None:
    if callback_query_id.startswith("1"):
        metis_log("bot", "answer_callback_skipped", reason="legacy_client")
        return
    payload: dict[str, object] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        response = _http.post(
            f"https://tapi.bale.ai/bot{token}/answerCallbackQuery",
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            metis_log("bot", "answer_callback_failed", detail=str(data)[:200])
    except Exception as err:
        metis_log("bot", "answer_callback_failed", error=str(err)[:200])


def _doctor_judgment_keyboard() -> dict:
    return {
        "keyboard": [
            [
                {"text": DOCTOR_APPROVE_BUTTON},
                {"text": DOCTOR_EDIT_BUTTON},
            ]
        ],
        "resize_keyboard": True,
    }


def _judgment_review_keyboard(session_id: int) -> dict:
    """Inline fallback (Bale answerCallbackQuery may fail on some clients)."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": DOCTOR_APPROVE_BUTTON,
                    "callback_data": f"{JUDGMENT_APPROVE_CALLBACK_PREFIX}{session_id}",
                },
                {
                    "text": DOCTOR_EDIT_BUTTON,
                    "callback_data": f"{JUDGMENT_EDIT_CALLBACK_PREFIX}{session_id}",
                },
            ]
        ]
    }


def _get_review_for_doctor_action(db: Database, session_id: int | None) -> JudgmentReview | None:
    if session_id is not None:
        return db.get_judgment_review(session_id)
    active = db.get_doctor_active_review_session_id()
    if active is not None:
        return db.get_judgment_review(active)
    return None


def _approve_judgment_review(
    db: Database,
    *,
    token: str,
    doctor_chat_id: str,
    session_id: int,
) -> None:
    review = db.get_judgment_review(session_id)
    if not review:
        _send_text(token, doctor_chat_id, "یافت نشد.")
        return
    if review.status == "sent":
        _send_text(token, doctor_chat_id, DOCTOR_ALREADY_SENT_ACK)
        return
    if _send_judgment_to_patient_from_review(
        db,
        token=token,
        review=review,
        text=review.judgment_text,
    ):
        _send_text(token, doctor_chat_id, DOCTOR_SENT_TO_PATIENT_ACK)
    else:
        _send_text(token, doctor_chat_id, DOCTOR_ALREADY_SENT_ACK)


def _start_judgment_edit(
    db: Database,
    *,
    token: str,
    doctor_chat_id: str,
    session_id: int,
) -> None:
    review = db.get_judgment_review(session_id)
    if not review:
        _send_text(token, doctor_chat_id, "یافت نشد.")
        return
    if review.status == "sent":
        _send_text(token, doctor_chat_id, DOCTOR_ALREADY_SENT_ACK)
        return
    db.set_judgment_review_status(session_id, "editing")
    db.set_doctor_active_review_session(session_id)
    _send_text(
        token,
        doctor_chat_id,
        f"{DOCTOR_EDIT_PROMPT}\n\nsid: {session_id}",
        reply_markup=_doctor_judgment_keyboard(),
    )


def _send_doctor_judgment_for_review(
    db: Database,
    *,
    token: str,
    doctor_chat_id: str,
    patient_chat_id: str,
    session_id: int,
    session_time: str,
    judgment: str,
    followup_escalation: bool = False,
) -> None:
    db.upsert_judgment_review(session_id, patient_chat_id, judgment)
    db.set_doctor_active_review_session(session_id)
    banner = ""
    if followup_escalation:
        banner = f"{DOCTOR_FOLLOWUP_ESCALATION_BANNER}\n\n"
    header = (
        f"pid: {patient_chat_id}\n"
        f"sid: {session_id}\n"
        f"time: {session_time}\n\n"
        f"{banner}"
        f"{judgment}\n\n"
        f"{DOCTOR_REVIEW_HINT}"
    )
    _send_text(
        token,
        doctor_chat_id,
        header,
        reply_markup=_doctor_judgment_keyboard(),
    )


def _strip_doctor_report_header(text: str) -> str:
    """Remove pid/sid/time metadata if doctor copy-pastes the full doctor message."""
    cleaned = text.strip()
    cleaned = re.sub(
        r"^pid:\s*\S+\s*\n"
        r"sid:\s*\S+\s*\n"
        r"time:\s*\S+\s*\n+",
        "",
        cleaned,
        count=1,
    )
    if DOCTOR_FOLLOWUP_ESCALATION_BANNER in cleaned:
        cleaned = cleaned.replace(f"{DOCTOR_FOLLOWUP_ESCALATION_BANNER}\n\n", "")
        cleaned = cleaned.replace(DOCTOR_FOLLOWUP_ESCALATION_BANNER, "")
    cleaned = strip_patient_approval_header(cleaned)
    if DOCTOR_REVIEW_HINT in cleaned:
        cleaned = cleaned.replace(DOCTOR_REVIEW_HINT, "").strip()
    return cleaned.strip()


def _deliver_judgment_to_patient(
    db: Database,
    *,
    token: str,
    session_id: int,
    patient_chat_id: str,
    text: str,
    doctor_bale_chat_id: str = "",
) -> None:
    judgment_body = _strip_doctor_report_header(text)
    doctor = resolve_reviewing_doctor(db, doctor_bale_chat_id)
    patient_text = format_judgment_for_patient(judgment_body, doctor)
    _send_text(token, patient_chat_id, patient_text, reply_markup=_chat_keyboard())
    db.append_message(session_id, "judgment_to_patient", patient_text)
    db.set_judgment_review_status(session_id, "sent")
    _schedule_followup_after_delivery(
        db,
        token=token,
        session_id=session_id,
        patient_chat_id=patient_chat_id,
        judgment_text=patient_text,
    )


def _schedule_followup_after_delivery(
    db: Database,
    *,
    token: str,
    session_id: int,
    patient_chat_id: str,
    judgment_text: str,
) -> None:
    if not needs_doctor_visit(judgment_text):
        return
    urgency_class = parse_urgency_class(judgment_text)
    due_at = compute_due_at(urgency_class)
    db.create_followup(session_id, patient_chat_id, due_at, urgency_class)
    doctor_chat_id = _resolve_doctor_chat_id(db)
    if doctor_chat_id:
        _send_text(
            token,
            doctor_chat_id,
            doctor_schedule_message(session_id, urgency_class),
        )


def _judgment_to_patient_text(session_messages: list[dict[str, str]]) -> str:
    for item in reversed(session_messages):
        if item.get("role") == "judgment_to_patient":
            return (item.get("text") or "").strip()
    return ""


def _start_followup_conversation(
    db: Database,
    *,
    token: str,
    followup: Followup,
) -> None:
    session = db.get_session_by_id(followup.session_id)
    if not session:
        db.update_followup(followup.session_id, status="cancelled")
        return
    latest = db.get_latest_session(followup.patient_chat_id)
    if not latest or latest.id != followup.session_id:
        db.update_followup(followup.session_id, status="cancelled")
        return
    if not db.try_claim_due_followup(followup.session_id):
        return
    intro = build_intro(session.chief_complaint)
    _send_text(
        token,
        followup.patient_chat_id,
        intro,
        reply_markup=trend_keyboard(followup.session_id),
    )
    db.append_message(followup.session_id, "followup_bot", intro)


def _process_due_followups(db: Database, *, token: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    for followup in db.get_due_followups(now_iso):
        try:
            _start_followup_conversation(db, token=token, followup=followup)
        except Exception as err:
            metis_log(
                "bot",
                "followup_start_error",
                session_id=followup.session_id,
                error=str(err)[:300],
            )


def _send_followup_redflag_question(
    db: Database,
    *,
    token: str,
    session_id: int,
    patient_chat_id: str,
) -> None:
    session = db.get_session_by_id(session_id)
    if not session:
        return
    judgment_text = _judgment_to_patient_text(session.messages)
    question = build_redflag_question(judgment_text)
    _send_text(
        token,
        patient_chat_id,
        question,
        reply_markup=redflag_keyboard(session_id),
    )
    db.append_message(session_id, "followup_bot", question)
    db.update_followup(session_id, step="redflag")


def _complete_followup(
    db: Database,
    *,
    token: str,
    session_id: int,
    patient_chat_id: str,
    trend: str = "",
) -> None:
    closing = build_closing_message(trend)
    _send_text(token, patient_chat_id, closing, reply_markup=_chat_keyboard())
    db.append_message(session_id, "followup_bot", closing)
    db.update_followup(session_id, status="completed", step="")


def _escalate_followup_to_triage(
    db: Database,
    deepseek: DeepSeekClient,
    *,
    token: str,
    patient_chat_id: str,
    session_id: int,
) -> None:
    db.update_followup(session_id, status="escalated", step="")
    db.update_session(
        session_id,
        current_phase=1,
        waiting_for_continue=0,
        answer_buffer=[],
        pending_field="",
    )
    deepseek.reset_session(patient_chat_id)
    _send_text(
        token,
        patient_chat_id,
        CLOSING_ESCALATION_INTRO,
        reply_markup=_chat_keyboard(),
    )
    db.append_message(session_id, "followup_bot", CLOSING_ESCALATION_INTRO)
    session = db.get_session_by_id(session_id)
    if not session:
        return
    demographics = db.get_demographics(patient_chat_id)
    followup_context = _followup_context_for_session(db, session_id, session.messages)
    _send_phase_questions(
        db,
        deepseek,
        token=token,
        chat_id=patient_chat_id,
        session_id=session_id,
        phase=1,
        chief_complaint=session.chief_complaint,
        demographics=demographics,
        session_messages=session.messages,
        followup_context=followup_context,
    )


def _process_followup_callback(
    db: Database,
    deepseek: DeepSeekClient,
    formatter: MetisHistoryFormatterClient,
    judging: MetisJudgingClient,
    *,
    token: str,
    callback_query: dict,
) -> None:
    callback_id = str(callback_query.get("id") or "")
    data = str(callback_query.get("data") or "").strip()
    sender = callback_query.get("from") or callback_query.get("from_user") or {}
    patient_chat_id = str(sender.get("id") or "").strip()
    if not callback_id or not data or not patient_chat_id:
        return

    trend_parsed = parse_trend_callback(data)
    if trend_parsed:
        session_id, trend = trend_parsed
        followup = db.get_followup(session_id)
        if (
            not followup
            or followup.patient_chat_id != patient_chat_id
            or followup.status != "in_progress"
            or followup.step != "trend"
        ):
            _answer_callback_query(token, callback_id, text="پیگیری فعال نیست.")
            return
        label = trend_label(trend)
        db.append_message(session_id, "followup_user", label)
        db.update_followup(session_id, trend=trend, step="redflag")
        _answer_callback_query(token, callback_id, text="ثبت شد")
        _send_followup_redflag_question(
            db,
            token=token,
            session_id=session_id,
            patient_chat_id=patient_chat_id,
        )
        return

    redflag_parsed = parse_redflag_callback(data)
    if redflag_parsed:
        session_id, has_redflag = redflag_parsed
        followup = db.get_followup(session_id)
        if (
            not followup
            or followup.patient_chat_id != patient_chat_id
            or followup.status != "in_progress"
            or followup.step != "redflag"
        ):
            _answer_callback_query(token, callback_id, text="پیگیری فعال نیست.")
            return
        answer = "بله، دارم" if has_redflag else "خیر، ندارم"
        db.append_message(session_id, "followup_user", answer)
        _answer_callback_query(token, callback_id, text="ثبت شد")
        if should_escalate(trend=followup.trend, redflag=has_redflag):
            _escalate_followup_to_triage(
                db,
                deepseek,
                token=token,
                patient_chat_id=patient_chat_id,
                session_id=session_id,
            )
        else:
            _complete_followup(
                db,
                token=token,
                session_id=session_id,
                patient_chat_id=patient_chat_id,
                trend=followup.trend,
            )
        return


def _process_callback_query(
    db: Database,
    deepseek: DeepSeekClient,
    formatter: MetisHistoryFormatterClient,
    judging: MetisJudgingClient,
    *,
    token: str,
    callback_query: dict,
) -> None:
    data = str(callback_query.get("data") or "").strip()
    if data.startswith("fu:"):
        _process_followup_callback(
            db,
            deepseek,
            formatter,
            judging,
            token=token,
            callback_query=callback_query,
        )
        return
    _process_judgment_callback(db, token=token, callback_query=callback_query)


def _send_judgment_to_patient_from_review(
    db: Database,
    *,
    token: str,
    review,
    text: str,
) -> bool:
    if review.status == "sent":
        return False
    _deliver_judgment_to_patient(
        db,
        token=token,
        session_id=review.session_id,
        patient_chat_id=review.patient_chat_id,
        text=text,
        doctor_bale_chat_id=_resolve_doctor_chat_id(db),
    )
    return True


def _process_doctor_review_buttons(
    db: Database,
    *,
    token: str,
    doctor_chat_id: str,
    text: str,
) -> bool:
    if text not in (DOCTOR_APPROVE_BUTTON, DOCTOR_EDIT_BUTTON):
        return False
    review = _get_review_for_doctor_action(db, db.get_doctor_active_review_session_id())
    if not review:
        _send_text(token, doctor_chat_id, "نظری برای بررسی یافت نشد.")
        return True
    if text == DOCTOR_APPROVE_BUTTON:
        _approve_judgment_review(
            db,
            token=token,
            doctor_chat_id=doctor_chat_id,
            session_id=review.session_id,
        )
        return True
    _start_judgment_edit(
        db,
        token=token,
        doctor_chat_id=doctor_chat_id,
        session_id=review.session_id,
    )
    return True


def _process_doctor_edit_message(
    db: Database,
    *,
    token: str,
    doctor_chat_id: str,
    text: str,
) -> bool:
    review = db.get_editing_judgment_review()
    if not review:
        return False
    if not text.strip():
        _send_text(token, doctor_chat_id, "متن خالی است. دوباره ارسال کنید.")
        return True
    if _send_judgment_to_patient_from_review(
        db,
        token=token,
        review=review,
        text=text.strip(),
    ):
        _send_text(token, doctor_chat_id, DOCTOR_SENT_TO_PATIENT_ACK)
    else:
        _send_text(token, doctor_chat_id, DOCTOR_ALREADY_SENT_ACK)
    return True


def _process_judgment_callback(
    db: Database,
    *,
    token: str,
    callback_query: dict,
) -> None:
    callback_id = str(callback_query.get("id") or "")
    data = str(callback_query.get("data") or "").strip()
    sender = callback_query.get("from") or callback_query.get("from_user") or {}
    doctor_chat_id = str(sender.get("id") or "").strip()
    resolved_doctor = _resolve_doctor_chat_id(db)

    if not callback_id or not data or not doctor_chat_id:
        return
    if resolved_doctor and doctor_chat_id != resolved_doctor:
        _answer_callback_query(token, callback_id, text="فقط پزشک مجاز است.")
        return

    if data.startswith(JUDGMENT_APPROVE_CALLBACK_PREFIX):
        session_id = int(data.removeprefix(JUDGMENT_APPROVE_CALLBACK_PREFIX))
        _approve_judgment_review(
            db,
            token=token,
            doctor_chat_id=doctor_chat_id,
            session_id=session_id,
        )
        _answer_callback_query(token, callback_id)
        return

    if data.startswith(JUDGMENT_EDIT_CALLBACK_PREFIX):
        session_id = int(data.removeprefix(JUDGMENT_EDIT_CALLBACK_PREFIX))
        _start_judgment_edit(
            db,
            token=token,
            doctor_chat_id=doctor_chat_id,
            session_id=session_id,
        )
        _answer_callback_query(token, callback_id)
        return


def _chat_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": CONTINUE_BUTTON_TEXT}],
            [{"text": RESTART_BUTTON_TEXT}],
        ],
        "resize_keyboard": True,
    }


def _begin_patient_session(
    db: Database,
    deepseek: DeepSeekClient,
    *,
    token: str,
    chat_id: str,
    user_label: str,
) -> None:
    db.cancel_followups_for_chat(chat_id)
    deepseek.reset_session(chat_id)
    session_id = db.create_session(chat_id)
    db.append_message(session_id, "user", user_label)
    start_text = f"{START_MESSAGE}\n\n{CONTINUE_HINT_TEXT}"
    _send_text(
        token,
        chat_id,
        start_text,
        reply_markup=_chat_keyboard(),
    )
    db.append_message(session_id, "bot", start_text)
    db.update_session(
        session_id,
        waiting_for_continue=1,
        answer_buffer=[],
    )


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
    judging: MetisJudgingClient,
    *,
    token: str,
    chat_id: str,
    text: str,
    raw_message: dict | None = None,
) -> None:
    if raw_message:
        _maybe_register_doctor(db, raw_message)

    doctor_chat_id = _resolve_doctor_chat_id(db)
    if doctor_chat_id and chat_id == doctor_chat_id:
        if _process_doctor_edit_message(
            db,
            token=token,
            doctor_chat_id=doctor_chat_id,
            text=text,
        ):
            return
        if _process_doctor_review_buttons(
            db,
            token=token,
            doctor_chat_id=doctor_chat_id,
            text=text,
        ):
            return

    if text in ("/start", RESTART_BUTTON_TEXT):
        _begin_patient_session(
            db,
            deepseek,
            token=token,
            chat_id=chat_id,
            user_label=text,
        )
        return

    active_followup = db.get_active_followup_for_chat(chat_id)
    if active_followup and active_followup.status == "in_progress":
        db.append_message(active_followup.session_id, "followup_user", text)
        _send_text(
            token,
            chat_id,
            FOLLOWUP_USE_BUTTONS,
            reply_markup=trend_keyboard(active_followup.session_id)
            if active_followup.step == "trend"
            else redflag_keyboard(active_followup.session_id),
        )
        db.append_message(active_followup.session_id, "followup_bot", FOLLOWUP_USE_BUTTONS)
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
                    reply_markup=_chat_keyboard(),
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
                    judging,
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
        _send_text(token, chat_id, question, reply_markup=_chat_keyboard())
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
                        judging,
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
        _send_text(token, chat_id, DEEPSEEK_ERROR_MESSAGE, reply_markup=_chat_keyboard())
        db.append_message(session.id, "bot", DEEPSEEK_ERROR_MESSAGE)


def main() -> None:
    lock_handle = _acquire_single_instance_lock()
    load_dotenv()
    apply_metis_direct_network()
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
    metis_judging_bot_id = os.getenv("METIS_JUDGING_BOT_ID", "").strip()
    if not metis_judging_bot_id:
        raise RuntimeError("METIS_JUDGING_BOT_ID is missing in environment.")
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
    judging = MetisJudgingClient(
        api_key=deepseek_api_key,
        bot_id=metis_judging_bot_id,
    )
    print(f"Judging Metis bot configured: {metis_judging_bot_id}", flush=True)

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
    if is_followup_test_mode():
        print(
            "Follow-up TEST MODE: emergency=10s, urgent=1m, routine=5m (poll every 5s)",
            flush=True,
        )
    print("Bale bot is running (HTTP long polling).", flush=True)
    offset: int | None = None
    last_followup_poll = 0.0

    try:
        while True:
            try:
                now_mono = time.monotonic()
                if now_mono - last_followup_poll >= followup_poll_interval_sec():
                    _process_due_followups(db, token=token)
                    last_followup_poll = now_mono

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

                    callback_query = update.get("callback_query")
                    if callback_query:
                        try:
                            _process_callback_query(
                                db,
                                deepseek,
                                formatter,
                                judging,
                                token=token,
                                callback_query=callback_query,
                            )
                        except Exception as err:
                            metis_log("bot", "callback_error", error=str(err)[:300])
                        continue

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
                        judging,
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
