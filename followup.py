"""Follow-up scheduling, parsing, and patient message templates."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

URGENCY_HOURS: dict[str, int] = {
    "emergency": 6,
    "urgent": 24,
    "routine": 72,
}

URGENCY_TEST_SECONDS: dict[str, int] = {
    "emergency": 10,
    "urgent": 60,
    "routine": 300,
}

TREND_BETTER = "better"
TREND_SAME = "same"
TREND_WORSE = "worse"

FOLLOWUP_TREND_CALLBACK_PREFIX = "fu:t:"
FOLLOWUP_REDFLAG_CALLBACK_PREFIX = "fu:r:"

FOLLOWUP_USE_BUTTONS = "لطفاً یکی از دکمه‌های زیر را انتخاب کنید."

CLOSING_ESCALATION_INTRO = (
    "با توجه به پاسخ شما، برای بررسی دقیق‌تر چند سوال تکمیلی می‌پرسم. "
    "لطفاً با دقت پاسخ دهید."
)

DOCTOR_FOLLOWUP_ESCALATION_BANNER = (
    "📋 پیگیری (Follow-up) — بیمار قبلی، همان جلسه، نه مورد جدید"
)

_SKIP_FOLLOWUP_BOT_TEXTS = frozenset({CLOSING_ESCALATION_INTRO, FOLLOWUP_USE_BUTTONS})


def build_closing_message(trend: str) -> str:
    tail = "اگر علائم جدیدی دیدید یا نگران شدید، هر وقت بخواهید دوباره پیام بدهید."
    if trend == TREND_BETTER:
        return f"خوشحالم که حالتان بهتر شده. {tail}"
    if trend == TREND_SAME:
        return f"ممنون که خبر دادید. امیدوارم به‌زودی بهتر شوید. {tail}"
    return f"ممنون که وقت گذاشتید و پاسخ دادید. {tail}"


def _is_skip_followup_bot_text(text: str) -> bool:
    if text in _SKIP_FOLLOWUP_BOT_TEXTS:
        return True
    if "هر وقت بخواهید دوباره پیام بدهید" in text:
        return True
    return False


def session_has_followup(messages: list[dict]) -> bool:
    return any(str(m.get("role", "")).startswith("followup_") for m in messages)


def _parse_iso(ts: str) -> datetime | None:
    raw = (ts or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_elapsed_fa(start: datetime, end: datetime) -> str:
    secs = max(0, int((end - start).total_seconds()))
    if secs < 60:
        return f"{secs} ثانیه"
    mins = secs // 60
    if mins < 60:
        return f"{mins} دقیقه"
    hours = mins // 60
    rem = mins % 60
    if rem:
        return f"{hours} ساعت و {rem} دقیقه"
    return f"{hours} ساعت"


def _scheduled_interval_label(urgency_class: str) -> str:
    if is_followup_test_mode():
        secs = URGENCY_TEST_SECONDS.get(urgency_class, 300)
        if secs < 60:
            return f"{secs} ثانیه (حالت تست)"
        return f"{secs // 60} دقیقه (حالت تست)"
    hours = hours_for_urgency(urgency_class)
    labels = {
        "emergency": f"{hours} ساعت (اورژانسی)",
        "urgent": f"{hours} ساعت (فوری)",
        "routine": f"{hours} ساعت (غیرفوری)",
    }
    return labels.get(urgency_class, f"{hours} ساعت")


def _extract_followup_qa(messages: list[dict]) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    pending_question = ""
    for item in messages:
        role = str(item.get("role") or "")
        text = (item.get("text") or "").strip()
        if not text:
            continue
        if role == "followup_bot":
            if _is_skip_followup_bot_text(text):
                pending_question = ""
                continue
            pending_question = text
        elif role == "followup_user" and pending_question:
            pairs.append({"question": pending_question, "answer": text})
            pending_question = ""
    return pairs


def _first_judgment_to_patient_time(messages: list[dict]) -> datetime | None:
    for item in messages:
        if item.get("role") == "judgment_to_patient":
            dt = _parse_iso(str(item.get("timestamp") or ""))
            if dt:
                return dt
    return None


def _followup_start_time(messages: list[dict]) -> datetime | None:
    for item in messages:
        if item.get("role") != "followup_bot":
            continue
        text = (item.get("text") or "").strip()
        if not text or _is_skip_followup_bot_text(text):
            continue
        dt = _parse_iso(str(item.get("timestamp") or ""))
        if dt:
            return dt
    return None


def build_followup_ai_context(
    messages: list[dict],
    *,
    urgency_class: str = "",
    trend: str = "",
) -> dict | None:
    """Structured follow-up summary for history taker and judging bots."""
    if not session_has_followup(messages):
        return None

    qa = _extract_followup_qa(messages)
    judgment_at = _first_judgment_to_patient_time(messages)
    followup_at = _followup_start_time(messages)
    now = datetime.now(timezone.utc)

    elapsed_judgment = ""
    if judgment_at and followup_at:
        elapsed_judgment = _format_elapsed_fa(judgment_at, followup_at)
    elif judgment_at:
        elapsed_judgment = _format_elapsed_fa(judgment_at, now)

    trend_fa = trend_label(trend) if trend else ""
    if not trend_fa and qa:
        for pair in qa:
            ans = pair["answer"]
            if ans in ("بهتر شدم", "مثل قبل", "بدتر شده"):
                trend_fa = ans
                break

    red_flags = ""
    for pair in qa:
        if "علائم هشدار" in pair["question"] or "علائم جدید" in pair["question"]:
            red_flags = pair["answer"]
            break

    return {
        "is_followup_escalation": True,
        "instruction_fa": (
            "این بیمار در پیگیری پس از قضاوت قبلی است (همان جلسه، نه مورد جدید). "
            "سوالات و قضاوت جدید باید با توجه به پاسخ‌های پیگیری و شرح حال قبلی باشد."
        ),
        "scheduled_followup_interval_fa": _scheduled_interval_label(urgency_class)
        if urgency_class
        else "",
        "elapsed_since_previous_judgment_fa": elapsed_judgment,
        "patient_trend_fa": trend_fa,
        "red_flags_reported_fa": red_flags,
        "followup_qa": qa,
    }


def format_followup_context_text(ctx: dict) -> str:
    lines = [
        "followup_context:",
        f"  is_followup_escalation: {ctx.get('is_followup_escalation')}",
        f"  instruction: {ctx.get('instruction_fa')}",
    ]
    if ctx.get("scheduled_followup_interval_fa"):
        lines.append(
            f"  scheduled_followup_interval: {ctx['scheduled_followup_interval_fa']}"
        )
    if ctx.get("elapsed_since_previous_judgment_fa"):
        lines.append(
            "  elapsed_since_previous_judgment: "
            f"{ctx['elapsed_since_previous_judgment_fa']}"
        )
    if ctx.get("patient_trend_fa"):
        lines.append(f"  patient_trend: {ctx['patient_trend_fa']}")
    if ctx.get("red_flags_reported_fa"):
        lines.append(f"  red_flags_reported: {ctx['red_flags_reported_fa']}")
    lines.append("  followup_questions_and_answers:")
    for i, pair in enumerate(ctx.get("followup_qa") or [], 1):
        lines.append(f"    Q{i}: {pair['question']}")
        lines.append(f"    A{i}: {pair['answer']}")
    return "\n".join(lines)


def _persian_digit(n: int) -> str:
    return "۱۲۳۴۵۶"[n - 1]


def parse_judgment_item(judgment_text: str, item_num: int) -> str:
    """Extract body text for numbered judgment item (1–6)."""
    if not judgment_text.strip() or item_num < 1 or item_num > 6:
        return ""
    pn = _persian_digit(item_num)
    an = str(item_num)
    pattern = (
        rf"\*\*[{pn}{an}]\.\s*[^*]+\*\*\s*[:\：]?\s*"
        rf"(.*?)(?=\n\n|\n\*\*[۱۲۳۴۵۶1-6]\.|\n🔗|\Z)"
    )
    match = re.search(pattern, judgment_text, re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1).strip())


def parse_urgency_class(judgment_text: str) -> str:
    """Classify urgency from item 2 only (avoid false match on اورژانس in items 5–6)."""
    item2 = parse_judgment_item(judgment_text, 2).lower()
    if not item2:
        return "routine"
    if "اورژانسی" in item2:
        return "emergency"
    if re.match(r"^\s*اورژانس", item2):
        return "emergency"
    if "فوری" in item2:
        return "urgent"
    if "غیرفوری" in item2 or "عادی" in item2:
        return "routine"
    return "routine"


def needs_doctor_visit(judgment_text: str) -> bool:
    item1 = parse_judgment_item(judgment_text, 1).lower().strip()
    if not item1:
        return True
    if item1.startswith("بله") or item1.startswith("بله،"):
        return True
    no_visit_phrases = (
        "خیر",
        "نیازی نیست",
        "نیاز نیست",
        "لازم نیست",
        "نیازی به مراجعه نیست",
        "نیازی به ویزیت نیست",
    )
    if any(item1.startswith(p) for p in no_visit_phrases):
        return False
    if any(p in item1 for p in ("نیازی نیست", "نیاز نیست", "لازم نیست")):
        return False
    # Standalone «نه» only — not as substring of نفس، نهاد، etc.
    if re.search(r"(^|[،.\s])نه($|[،.\s])", item1):
        return False
    return True


def is_followup_test_mode() -> bool:
    return os.getenv("FOLLOWUP_TEST_MODE", "").strip().lower() in {"1", "true", "yes"}


def followup_poll_interval_sec() -> int:
    return 5 if is_followup_test_mode() else 60


def hours_for_urgency(urgency_class: str) -> int:
    return URGENCY_HOURS.get(urgency_class, 72)


def delay_seconds_for_urgency(urgency_class: str) -> int:
    if is_followup_test_mode():
        return URGENCY_TEST_SECONDS.get(urgency_class, 300)
    return hours_for_urgency(urgency_class) * 3600


def compute_due_at(urgency_class: str, *, now: datetime | None = None) -> str:
    base = now or datetime.now(timezone.utc)
    seconds = delay_seconds_for_urgency(urgency_class)
    return (base + timedelta(seconds=seconds)).isoformat()


def build_intro(chief_complaint: str) -> str:
    cc = chief_complaint.strip() or "مشکل سلامتی‌تان"
    return (
        f"سلام، امیدوارم حالتان خوب باشد.\n\n"
        f"مدتی پیش دربارهٔ «{cc}» با هم صحبت کردیم. "
        f"می‌خواستم بدانم الان حال‌تان چطور است؟"
    )


def build_redflag_question(judgment_text: str) -> str:
    item6 = parse_judgment_item(judgment_text, 6).strip()
    if item6:
        return (
            "ممنون که پاسخ دادید.\n\n"
            "آیا هر کدام از علائم هشدار زیر را الان دارید؟\n\n"
            f"{item6}"
        )
    return (
        "ممنون که پاسخ دادید.\n\n"
        "آیا علائم جدید یا شدیدتری نسبت به قبل دارید؟"
    )


def trend_label(trend: str) -> str:
    return {
        TREND_BETTER: "بهتر شدم",
        TREND_SAME: "مثل قبل",
        TREND_WORSE: "بدتر شده",
    }.get(trend, trend)


def trend_keyboard(session_id: int) -> dict:
    sid = str(session_id)
    return {
        "inline_keyboard": [
            [
                {"text": "😊 بهتر شدم", "callback_data": f"fu:t:b:{sid}"},
                {"text": "مثل قبل", "callback_data": f"fu:t:s:{sid}"},
                {"text": "😟 بدتر شده", "callback_data": f"fu:t:w:{sid}"},
            ]
        ]
    }


def redflag_keyboard(session_id: int) -> dict:
    sid = str(session_id)
    return {
        "inline_keyboard": [
            [
                {"text": "بله، دارم", "callback_data": f"fu:r:y:{sid}"},
                {"text": "خیر، ندارم", "callback_data": f"fu:r:n:{sid}"},
            ]
        ]
    }


def parse_trend_callback(data: str) -> tuple[int, str] | None:
    # fu:t:b:123
    match = re.fullmatch(r"fu:t:([bsw]):(\d+)", data)
    if not match:
        return None
    code, sid = match.group(1), int(match.group(2))
    trend = {"b": TREND_BETTER, "s": TREND_SAME, "w": TREND_WORSE}[code]
    return sid, trend


def parse_redflag_callback(data: str) -> tuple[int, bool] | None:
    match = re.fullmatch(r"fu:r:([yn]):(\d+)", data)
    if not match:
        return None
    code, sid = match.group(1), int(match.group(2))
    return sid, code == "y"


def doctor_schedule_message(session_id: int, urgency_class: str) -> str:
    if is_followup_test_mode():
        seconds = delay_seconds_for_urgency(urgency_class)
        if seconds < 60:
            delay_label = f"{seconds} ثانیه"
        else:
            delay_label = f"{seconds // 60} دقیقه"
        test_labels = {
            "emergency": "اورژانسی (تست: ۱۰ ثانیه)",
            "urgent": "فوری (تست: ۱ دقیقه)",
            "routine": "غیرفوری (تست: ۵ دقیقه)",
        }
        label = test_labels.get(urgency_class, delay_label)
        return (
            f"📅 [تست] پیگیری خودکار در {delay_label} برنامه‌ریزی شد ({label}).\n"
            f"sid: {session_id}"
        )
    hours = hours_for_urgency(urgency_class)
    labels = {
        "emergency": "اورژانسی (۶ ساعت)",
        "urgent": "فوری (۲۴ ساعت)",
        "routine": "غیرفوری (۷۲ ساعت)",
    }
    label = labels.get(urgency_class, f"{hours} ساعت")
    return (
        f"📅 پیگیری خودکار برای این بیمار در {hours} ساعت برنامه‌ریزی شد "
        f"({label}).\nsid: {session_id}"
    )


def should_escalate(*, trend: str, redflag: bool) -> bool:
    return trend == TREND_WORSE or redflag
