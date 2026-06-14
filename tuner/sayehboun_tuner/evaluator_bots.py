import os


def history_taker_evaluator_bot_id() -> str:
    return (
        os.getenv("METIS_HISTORY_TAKER_EVALUATOR_BOT_ID", "").strip()
        or os.getenv("METIS_EVALUATOR_BOT_ID", "").strip()
    )


def judging_evaluator_bot_id() -> str:
    return os.getenv("METIS_JUDGING_EVALUATOR_BOT_ID", "").strip()


def check_evaluator_bot_separation(
    *,
    history_evaluator_bot_id: str,
    judging_evaluator_bot_id: str,
) -> str | None:
    if not history_evaluator_bot_id or not judging_evaluator_bot_id:
        return None
    if history_evaluator_bot_id == judging_evaluator_bot_id:
        return (
            "History and judging evaluator bot IDs must differ. "
            "Set METIS_HISTORY_TAKER_EVALUATOR_BOT_ID (or METIS_EVALUATOR_BOT_ID) "
            "and METIS_JUDGING_EVALUATOR_BOT_ID to separate Metis tuner bots."
        )
    return None
