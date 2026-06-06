import time

from metis_utils import create_metis_bot, metis_log, send_message_with_retry


class MetisJudgingClient:
    def __init__(self, api_key: str, bot_id: str) -> None:
        self.bot = create_metis_bot(api_key=api_key, bot_id=bot_id)
        self._session = None

    def judge(self, payload_text: str) -> str:
        def get_session():
            if self._session is None:
                metis_log("judging", "create_session_start")
                t0 = time.time()
                self._session = self.bot.create_session()
                metis_log(
                    "judging",
                    "create_session_ok",
                    session_id=str(self._session.id),
                    elapsed_s=round(time.time() - t0, 2),
                )
            return self._session

        def reset_session():
            session = self._session
            self._session = None
            if session is None:
                return
            try:
                self.bot.delete_session(session)
            except Exception:
                pass

        try:
            return send_message_with_retry(
                self.bot,
                payload_text,
                get_session=get_session,
                reset_session=reset_session,
                service="judging",
            )
        finally:
            reset_session()
