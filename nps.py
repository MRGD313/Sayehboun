"""NPS surveys for patients and doctors."""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

SURVEY_PATIENT = "patient"
SURVEY_DOCTOR = "doctor"

TRIGGER_JUDGMENT_DELIVERED = "judgment_delivered"
TRIGGER_DOCTOR_REVIEW_SENT = "doctor_review_sent"

NPS_USE_BUTTONS = "لطفاً یکی از اعداد ۰ تا ۱۰ را انتخاب کنید."

NPS_SKIP_COMMENT = "رد کردن"

PATIENT_DELAY_HOURS = 4
PATIENT_TEST_SECONDS = 120
DOCTOR_DELAY_SECONDS = 3

PATIENT_QUESTION = (
    "از ۰ تا ۱۰، چقدر احتمال دارد سایه‌بون را به خانواده یا دوستانتان پیشنهاد دهید؟\n\n"
    "سادگی فرآیند و آرامش شما برای ما مهم است."
)

DOCTOR_QUESTION = (
    "از ۰ تا ۱۰، تجربه طبابت در سایه‌بون چقدر رضایت‌بخش بود؟"
)


def is_nps_test_mode() -> bool:
    return os.getenv("FOLLOWUP_TEST_MODE", "").strip().lower() in {"1", "true", "yes"}


def patient_nps_due_at(*, now: datetime | None = None) -> str:
    base = now or datetime.now(timezone.utc)
    if is_nps_test_mode():
        return (base + timedelta(seconds=PATIENT_TEST_SECONDS)).isoformat()
    return (base + timedelta(hours=PATIENT_DELAY_HOURS)).isoformat()


def doctor_nps_due_at(*, now: datetime | None = None) -> str:
    base = now or datetime.now(timezone.utc)
    return (base + timedelta(seconds=DOCTOR_DELAY_SECONDS)).isoformat()


def build_comment_prompt(score: int) -> str:
    if score <= 6:
        return "ممنون. اگر مایلید، در یک جمله بگویید چه چیزی بهتر شود (اختیاری)."
    if score >= 9:
        return "ممنون. اگر مایلید، در یک جمله بگویید چه چیزی بیشترین کمک را کرد (اختیاری)."
    return "ممنون. اگر مایلید، در یک جمله بگویید چرا این امتیاز را دادید (اختیاری)."


def build_thanks(survey_type: str) -> str:
    if survey_type == SURVEY_DOCTOR:
        return "از بازخورد شما سپاسگزاریم. نظر شما به بهتر شدن سایه کمک می‌کند."
    return "از وقتی که گذاشتید سپاسگزاریم. امیدواریم همیشه در آرامش باشید."


def score_keyboard(survey_id: int) -> dict:
    sid = str(survey_id)

    def btn(score: int) -> dict:
        return {"text": str(score), "callback_data": f"nps:sc:{sid}:{score}"}

    return {
        "inline_keyboard": [
            [btn(0), btn(1), btn(2), btn(3), btn(4), btn(5)],
            [btn(6), btn(7), btn(8), btn(9), btn(10)],
        ]
    }


def comment_keyboard(survey_id: int) -> dict:
    sid = str(survey_id)
    return {
        "inline_keyboard": [[{"text": NPS_SKIP_COMMENT, "callback_data": f"nps:sk:{sid}"}]]
    }


def parse_score_callback(data: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"nps:sc:(\d+):(\d+)", data)
    if not match:
        return None
    survey_id = int(match.group(1))
    score = int(match.group(2))
    if score < 0 or score > 10:
        return None
    return survey_id, score


def parse_skip_callback(data: str) -> int | None:
    match = re.fullmatch(r"nps:sk:(\d+)", data)
    if not match:
        return None
    return int(match.group(1))


def score_label(score: int) -> str:
    return f"امتیاز: {score}"
