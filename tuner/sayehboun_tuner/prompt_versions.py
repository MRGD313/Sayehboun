import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def versions_dir(tuner_root: Path) -> Path:
    return tuner_root / "prompts" / "versions"


def manifest_path(tuner_root: Path) -> Path:
    return versions_dir(tuner_root) / "manifest.json"


def load_manifest(tuner_root: Path) -> dict[str, Any]:
    path = manifest_path(tuner_root)
    if not path.exists():
        return {"current": "v1", "versions": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(tuner_root: Path, manifest: dict[str, Any]) -> None:
    path = manifest_path(tuner_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_version(version: str) -> str:
    version = version.strip().lower()
    if not version.startswith("v"):
        version = f"v{version}"
    if not re.fullmatch(r"v\d+", version):
        raise ValueError(f"Invalid version label: {version!r} (use v1, v2, ...)")
    return version


def prompt_file_for_version(tuner_root: Path, version: str) -> Path:
    version = normalize_version(version)
    manifest = load_manifest(tuner_root)
    entry = manifest.get("versions", {}).get(version)
    if entry and entry.get("file"):
        return versions_dir(tuner_root) / entry["file"]
    return versions_dir(tuner_root) / f"history_taker_{version}.txt"


def get_current_version(tuner_root: Path) -> str:
    manifest = load_manifest(tuner_root)
    return str(manifest.get("current") or "v1")


def read_prompt_text(tuner_root: Path, version: str) -> str:
    path = prompt_file_for_version(tuner_root, version)
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def register_version(
    tuner_root: Path,
    version: str,
    *,
    source_file: Path,
    note: str = "",
    source_session_id: int | None = None,
    set_current: bool = False,
) -> Path:
    version = normalize_version(version)
    dest = versions_dir(tuner_root) / f"history_taker_{version}.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = source_file.read_text(encoding="utf-8").strip()
    dest.write_text(text, encoding="utf-8")

    manifest = load_manifest(tuner_root)
    versions = manifest.setdefault("versions", {})
    entry: dict[str, Any] = {
        "file": dest.name,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "note": note,
        "applied_to_metis": False,
    }
    if source_session_id is not None:
        entry["source_session_id"] = source_session_id
    versions[version] = entry
    if set_current:
        manifest["current"] = version
    save_manifest(tuner_root, manifest)
    return dest


def set_current_version(tuner_root: Path, version: str) -> None:
    version = normalize_version(version)
    manifest = load_manifest(tuner_root)
    if version not in manifest.get("versions", {}):
        raise ValueError(f"Version {version} is not registered in manifest.json")
    manifest["current"] = version
    save_manifest(tuner_root, manifest)


def update_env_prompt_version(sayehboun_root: Path, version: str) -> None:
    version = normalize_version(version)
    env_path = sayehboun_root / ".env"
    if not env_path.exists():
        return
    lines = env_path.read_text(encoding="utf-8").splitlines()
    updated = False
    for idx, line in enumerate(lines):
        if line.startswith("PROMPT_VERSION="):
            lines[idx] = f"PROMPT_VERSION={version}"
            updated = True
            break
    if not updated:
        lines.append(f"PROMPT_VERSION={version}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_current_marker(tuner_root: Path, version: str) -> None:
    path = versions_dir(tuner_root) / "CURRENT"
    path.write_text(normalize_version(version) + "\n", encoding="utf-8")
