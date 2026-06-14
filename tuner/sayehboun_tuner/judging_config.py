from dataclasses import dataclass
from pathlib import Path
import os

from sayehboun_tuner.evaluator_bots import judging_evaluator_bot_id


@dataclass
class JudgingTunerSettings:
    tuner_root: Path
    sayehboun_root: Path
    api_key: str
    judging_bot_id: str
    judging_evaluator_bot_id: str
    db_path: Path
    judging_prompt_version: str
    judging_model: str
    report_dir: Path


def load_judging_settings() -> JudgingTunerSettings:
    tuner_root = Path(__file__).resolve().parent.parent
    sayehboun_root = tuner_root.parent

    api_key = os.getenv("METIS_API_KEY", "").strip() or os.getenv("DEEPSEEK_API_KEY", "").strip()
    judging_bot_id = os.getenv("METIS_JUDGING_BOT_ID", "").strip()

    db_raw = os.getenv("SQLITE_DB_PATH", "bot.db").strip()
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = (sayehboun_root / db_path).resolve()

    judging_model = (
        os.getenv("JUDGING_MODEL", "").strip()
        or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    )

    return JudgingTunerSettings(
        tuner_root=tuner_root,
        sayehboun_root=sayehboun_root,
        api_key=api_key,
        judging_bot_id=judging_bot_id,
        judging_evaluator_bot_id=judging_evaluator_bot_id(),
        db_path=db_path,
        judging_prompt_version=os.getenv("JUDGING_PROMPT_VERSION", "v1").strip(),
        judging_model=judging_model,
        report_dir=tuner_root / "judging_reports",
    )
