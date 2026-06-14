from dataclasses import dataclass
from pathlib import Path
import os

from sayehboun_tuner.evaluator_bots import history_taker_evaluator_bot_id


@dataclass
class Settings:
    tuner_root: Path
    sayehboun_root: Path
    api_key: str
    history_taker_bot_id: str
    evaluator_bot_id: str
    staging_bot_id: str
    db_path: Path
    prompt_version: str
    history_taker_model: str
    report_dir: Path
    allow_incomplete: bool = False

    @property
    def project_root(self) -> Path:
        """Alias for tuner_root (prompts live here)."""
        return self.tuner_root


def load_settings() -> Settings:
    tuner_root = Path(__file__).resolve().parent.parent
    sayehboun_root = tuner_root.parent

    api_key = os.getenv("METIS_API_KEY", "").strip()
    if not api_key:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()

    history_taker_bot_id = (
        os.getenv("METIS_HISTORY_TAKER_BOT_ID", "").strip()
        or os.getenv("METIS_BOT_ID", "").strip()
    )

    db_raw = os.getenv("SQLITE_DB_PATH", "bot.db").strip()
    db_path = Path(db_raw)
    if not db_path.is_absolute():
        db_path = (sayehboun_root / db_path).resolve()

    report_dir = tuner_root / "reports"

    history_taker_model = (
        os.getenv("HISTORY_TAKER_MODEL", "").strip()
        or os.getenv("DEEPSEEK_MODEL", "deepseek-chat").strip()
    )

    return Settings(
        tuner_root=tuner_root,
        sayehboun_root=sayehboun_root,
        api_key=api_key,
        history_taker_bot_id=history_taker_bot_id,
        evaluator_bot_id=history_taker_evaluator_bot_id(),
        staging_bot_id=os.getenv("METIS_HISTORY_TAKER_STAGING_BOT_ID", "").strip(),
        db_path=db_path,
        prompt_version=os.getenv("PROMPT_VERSION", "v1").strip(),
        history_taker_model=history_taker_model,
        report_dir=report_dir,
    )
