import json
import time
from typing import Any

from metis_utils import (
    METIS_MAX_ATTEMPTS,
    create_metis_bot,
    metis_log,
    send_message_with_retry,
)

TRIAGE_SYSTEM_PROMPT = """Role: شما یک دستیار هوش مصنوعی تریژ بالینی (Clinical Triage Assistant) هستید که وظیفه دارید با طرح سؤالات دقیق، هدفمند و فازی**، شرح حال کاملی از بیمار جمع‌آوری کنید.

Goal: هدف نهایی شما هدایت یک فرآیند پرسش و پاسخ پویا و چندمرحله‌ای است. این فرآیند باید در چند فاز کوتاه انجام شود تا بیمار خسته نشود، اما در نهایت، پزشک با مشاهده مجموعه پاسخ‌ها، بتواند بدون نیاز به پرسیدن سؤال اضافه، قضاوت بالینی خود را انجام دهد.

**Input Data: شما اطلاعات زیر را در قالب زیر دریافت می‌کنید:
1. شکایت اصلی: [پاسخ بیمار به سوال: دچار چه بیماری یا علامتی شدید؟]
2. سن: [پاسخ بیمار]
3. جنسیت: [پاسخ بیمار]
4. شغل: [پاسخ بیمار]
5. داروهای مصرفی: [پاسخ بیمار]
6. مدارک پزشکی: [پاسخ بیمار]
7. بیماری زمینه ای/سابقه جراحی: [پاسخ بیمار]

---
Mandatory Priority (Red Flags) & Phasing Rules:
شما فرآیند را در فازهای مجزا اجرا می‌کنید. در هر مرحله، فقط یک لیست کوتاه از سؤالات را مطرح می‌کنید و منتظر پاسخ بیمار می‌مانید تا بر اساس پاسخ‌ها، سؤالات فاز بعدی را فیلتر و مطرح کنید.

فاز ۱: Red Flags و علائم حیاتی (حداکثر ۵ سوال)
* این فاز اجباری و اولویت اول است.
* شروع فرآیند با سؤالاتی است که علائم و نشانه‌های اورژانسی (Red Flags) مرتبط با شکایت اصلی (و بیماری‌های محتمل آن، Differential Diagnosis) را بررسی کند.

فاز ۲: جزئیات شکایت اصلی (O-L-D C-A-R-T-S) (حداکثر 6 سوال)
* فقط پس از اتمام فاز ۱ (دریافت پاسخ‌ها)، وارد این فاز شوید.
* سؤالات این فاز به تکمیل جزئیات شکایت اصلی (شروع، سیر، شدت، محل دقیق، عوامل تشدید کننده/تسکین‌دهنده، علائم همراه) می‌پردازند.

فاز ۳: سوابق و زمینه (حداکثر 4 سوال)
* فقط پس از اتمام فاز ۲ وارد این فاز شوید.
* سؤالات لازم برای جمع‌آوری سوابق پزشکی، داروهای مصرفی و سابقه جراحی/بیماری‌های زمینه‌ای مرتبط با شکایت را بپرسید.

---
Output Format:
خروجی شما باید تنها شامل یک لیست شماره‌دار از سؤالات مربوط به فاز کنونی باشد.

* زبان: ساده، روان و قابل فهم برای یک فرد غیرپزشک (بیمار) و فقط فارسی.
* تعداد: در هر فاز حداکثر ۵ سوال بپرسید.
* قالب‌بندی: قبل از شماره هر سوال، حتماً از ایموجی 🔶 استفاده کنید. (مثال: 🔶1. آیا...)
* فاصله خطی: حتماً بین هر سوال و سوال بعدی، یک خط فاصله خالی (New Line) قرار دهید.

Hard Rules (MUST FOLLOW) - قوانین غیرقابل نقض:
1. به هیچ عنوان، تحت هیچ شرایطی، نباید هیچ‌گونه تشخیص (Diagnosis)، قضاوت بالینی، توصیه پزشکی، یا توصیه‌ای برای مصرف دارو ارائه دهید. وظیفه شما فقط و فقط پرسیدن سؤال است.
2. خروجی شما فقط و فقط شامل لیست شماره‌دار سؤالات مربوط به فاز کنونی است و هیچ متن یا مقدمه دیگری نباید داشته باشد.
3. پیام پایانی:
   * در پایان فاز ۱ و فاز ۲: دقیقاً این پیام را اضافه کنید: "لطفاً به این سوالات پاسخ دهید تا بتوانم سوالات تکمیلی (فاز بعدی) را مطرح کنم."
   * در پایان فاز ۳: دقیقاً این پیام را اضافه کنید: "لطفاً به این موارد پاسخ دهید تا اطلاعات شما برای بررسی نهایی آماده شود."

---
حالا، بر اساس اطلاعات ورودي، فرآیند را از فاز مربوطه شروع کرده و سؤالات مربوط به فاز پرسیده نشده را تولید کنید."""

# One failed primary attempt -> switch to backup for rest of Bale session (/start resets).
PRIMARY_MAX_ATTEMPTS = 1
BACKUP_MAX_ATTEMPTS = 3


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        bot_id: str,
        *,
        backup_bot_id: str | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.primary_bot_id = bot_id
        self.backup_bot_id = (backup_bot_id or "").strip()
        self._primary_bot = create_metis_bot(api_key=api_key, bot_id=bot_id)
        self._backup_bot = (
            create_metis_bot(api_key=api_key, bot_id=self.backup_bot_id)
            if self.backup_bot_id
            else None
        )
        # chat_key -> {"primary": session|None, "backup": session|None}
        self._sessions: dict[str, dict[str, Any]] = {}
        # After primary fails once, use backup for all history-taker calls until /start.
        self._active_lane: dict[str, str] = {}

    @property
    def bot(self):
        """Primary Metis bot (compat)."""
        return self._primary_bot

    def _lane_key(self, lane: str) -> str:
        return "backup" if lane == "backup" else "primary"

    def _bot_for_lane(self, lane: str):
        if lane == "backup":
            if self._backup_bot is None:
                raise RuntimeError("Backup Metis bot is not configured.")
            return self._backup_bot
        return self._primary_bot

    def _ensure_session(self, chat_key: str, lane: str = "primary"):
        lane_key = self._lane_key(lane)
        chat_sessions = self._sessions.setdefault(chat_key, {})
        session = chat_sessions.get(lane_key)
        if session is None:
            bot = self._bot_for_lane(lane)
            service = "triage-backup" if lane == "backup" else "triage"
            metis_log(
                service,
                "create_session_start",
                chat_key=chat_key,
                bot_id=self.backup_bot_id if lane == "backup" else self.primary_bot_id,
            )
            t0 = time.time()
            session = bot.create_session()
            metis_log(
                service,
                "create_session_ok",
                chat_key=chat_key,
                session_id=str(session.id),
                elapsed_s=round(time.time() - t0, 2),
            )
            chat_sessions[lane_key] = session
        return session

    def reset_session(self, chat_key: str, lane: str | None = None) -> None:
        chat_sessions = self._sessions.get(chat_key)
        if not chat_sessions:
            return
        lanes = [self._lane_key(lane)] if lane else list(chat_sessions.keys())
        for lane_key in lanes:
            session = chat_sessions.pop(lane_key, None)
            if session is None:
                continue
            bot = self._primary_bot if lane_key == "primary" else self._backup_bot
            if bot is None:
                continue
            try:
                bot.delete_session(session)
            except Exception:
                pass
        if not chat_sessions:
            self._sessions.pop(chat_key, None)
        if lane is None:
            self._active_lane.pop(chat_key, None)

    def _get_active_lane(self, chat_key: str) -> str:
        lane = self._active_lane.get(chat_key, "primary")
        if lane == "backup" and self._backup_bot is None:
            return "primary"
        return lane

    def _send_prompt(
        self,
        chat_key: str,
        prompt: str,
        *,
        lane: str,
        max_attempts: int = METIS_MAX_ATTEMPTS,
    ) -> str:
        service = "triage-backup" if lane == "backup" else "triage"
        bot = self._bot_for_lane(lane)

        def get_session():
            return self._ensure_session(chat_key, lane)

        def reset_session():
            self.reset_session(chat_key, lane)

        return send_message_with_retry(
            bot,
            prompt,
            get_session=get_session,
            reset_session=reset_session,
            service=service,
            max_attempts=max_attempts,
        )

    def _send_with_fallback(self, chat_key: str, prompt: str) -> str:
        lane = self._get_active_lane(chat_key)
        if lane == "backup":
            metis_log("triage", "using_backup_lane", chat_key=chat_key)
            return self._send_prompt(
                chat_key,
                prompt,
                lane="backup",
                max_attempts=BACKUP_MAX_ATTEMPTS,
            )

        try:
            return self._send_prompt(
                chat_key,
                prompt,
                lane="primary",
                max_attempts=PRIMARY_MAX_ATTEMPTS,
            )
        except Exception as primary_err:
            if self._backup_bot is None:
                raise
            metis_log(
                "triage",
                "fallback_to_backup",
                chat_key=chat_key,
                primary_bot_id=self.primary_bot_id,
                backup_bot_id=self.backup_bot_id,
                reason=type(primary_err).__name__,
                note="primary_failed_once_using_backup_for_session",
            )
            self.reset_session(chat_key, "primary")
            self._active_lane[chat_key] = "backup"
            return self._send_prompt(
                chat_key,
                prompt,
                lane="backup",
                max_attempts=BACKUP_MAX_ATTEMPTS,
            )

    def generate_phase_questions(
        self,
        *,
        chat_key: str,
        current_phase: int,
        chief_complaint: str,
        demographics: dict[str, Any],
        session_messages: list[dict[str, Any]],
        followup_context: dict[str, Any] | None = None,
    ) -> str:
        clean_demographics = {
            key: value
            for key, value in demographics.items()
            if not str(key).startswith("_")
        }
        instruction = "فقط سوالات فاز فعلی را مطابق قوانین تولید کن."
        if followup_context:
            instruction = (
                f"{followup_context.get('instruction_fa', '')} "
                "فقط سوالات فاز فعلی را مطابق قوانین تولید کن."
            )
        payload: dict[str, Any] = {
            "current_phase": current_phase,
            "chief_complaint": chief_complaint,
            "demographics": clean_demographics,
            "session_messages": session_messages,
            "instruction": instruction,
        }
        if followup_context:
            payload["followup_context"] = followup_context

        # In Metis Bot mode, core instructions are configured in bot panel.
        # Send only compact dynamic data to avoid token waste/truncation.
        prompt = "اطلاعات ورودی:\n" + json.dumps(payload, ensure_ascii=False)
        metis_log(
            "triage",
            "build_prompt",
            phase=current_phase,
            prompt_chars=len(prompt),
            message_count=len(session_messages),
        )

        return self._send_with_fallback(chat_key, prompt)

    def decide_current_phase(
        self,
        *,
        chat_key: str,
        previous_phase: int,
        chief_complaint: str,
        demographics: dict[str, Any],
        session_messages: list[dict[str, Any]],
    ) -> int:
        decision_prompt = (
            "شما فقط باید فاز فعلی تریاژ را تعیین کنید.\n"
            "فقط یک عدد برگردان: 1 یا 2 یا 3.\n"
            "قاعده: بر اساس پاسخ‌های قبلی بیمار و سوالات قبلی دستیار، "
            "اولویت با اولین فاز تکمیل‌نشده است.\n"
            "اگر مطمئن نیستی، همان previous_phase را برگردان."
        )
        payload = {
            "previous_phase": previous_phase,
            "chief_complaint": chief_complaint,
            "demographics": demographics,
            "session_messages": session_messages,
        }
        prompt = (
            "فقط یک عدد 1 یا 2 یا 3 برای فاز فعلی برگردان.\n"
            "اگر مطمئن نیستی previous_phase را برگردان.\n\n"
            "اطلاعات:\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        content = self._send_with_fallback(chat_key, prompt).strip()
        if content.startswith("1"):
            return 1
        if content.startswith("2"):
            return 2
        if content.startswith("3"):
            return 3
        if "2" in content and "1" not in content and "3" not in content:
            return 2
        if "3" in content and "1" not in content and "2" not in content:
            return 3
        return max(1, min(3, previous_phase))
