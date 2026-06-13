# History Taker Prompt Tuner

Lives inside the Sayehboun project at `tuner/`. Reads **real sessions** from `bot.db`, calls the Metis **evaluator bot**, and writes reports with a **full revised History Taker prompt** for manual review.

Uses the main project `.env` at `sayehboun/.env` (same keys as the Bale bot + `METIS_EVALUATOR_BOT_ID`).

## Setup

Already uses root `requirements.txt` (`metisai`, `httpx`, `python-dotenv`).

Add to **`sayehboun/.env`** (project root):

```env
METIS_EVALUATOR_BOT_ID=your-evaluator-bot-id
PROMPT_VERSION=v3
```

`METIS_BOT_ID` / `DEEPSEEK_API_KEY` / `SQLITE_DB_PATH` are reused automatically.

Evaluator bot instructions: `tuner/prompts/evaluator_instructions.txt` (your text, unchanged).

**Evaluator model (Metis):** `google` / `gemini-3.1-pro-preview` (Gemini 3.1 Pro), summarizer empty.

## Prompt versioning

Stored under `tuner/prompts/versions/`:

| File | Purpose |
|------|---------|
| `history_taker_v1.txt` | Original prompt |
| `history_taker_v2.txt` | Tuner iteration from session 74 |
| `history_taker_v3.txt` | Triage-focused phases (current in Metis) |
| `manifest.json` | Version metadata + which is **current** |
| `CURRENT` | Quick marker (`v3`) |

**Current history taker: `v3`** (`.env` → `PROMPT_VERSION=v3`).

### History formatter

| File | Purpose |
|------|---------|
| `history_formatter_instructions.txt` | Current formatter prompt (Metis) |
| `history_formatter_v1.txt` | Versioned copy |
| `formatter_manifest.json` | Formatter version metadata |
| `FORMATTER_CURRENT` | Quick marker (`v1`) |

**Formatter model (Metis):** `deepseek` / `deepseek-v4-flash` — unchanged.

### Judging bot

| File | Purpose |
|------|---------|
| `judging_instructions.txt` | Current judging prompt (Metis) |
| `judging_v1.txt` | Versioned copy |
| `judging_manifest.json` | Judging version metadata |
| `JUDGING_CURRENT` | Quick marker (`v1`) |

**Judging model (Metis):** `deepseek` / `deepseek-v4-flash` — unchanged.

When you change the history taker Metis prompt, register a new version:

```powershell
py tune.py prompt register v3 --file reports\session_XX_revised_instructions.txt --note "your note" --session-id 74 --set-current
```

Or after manual Metis edit + fetch:

```powershell
py tune.py fetch-instructions --label v3-candidate
py tune.py prompt register v3 --file prompts\history_taker_snapshot_v3-candidate.txt --note "..." --set-current
```

```powershell
py tune.py prompt list
py tune.py prompt current
py tune.py prompt set-current v2
```

Tell the assistant: **"History taker prompt is now v3"** after you bump version.

## One session per tune run

Each DB session may be used **once** for history taker tuning. Used sessions are tracked in `tuner/tuned_sessions.json`.

- `list` shows a `tuned` column (`yes` / `no`)
- `run --session-id N` is rejected if that session was already tuned
- `run --last N` picks the **N most recent complete sessions that are not yet tuned**
- `run --session-id N --force` re-runs anyway (overwrites the registry entry)

## Commands (run from `tuner/`)

```powershell
cd c:\Users\MRGD\.cursor\projects\sayehboun\tuner

py tune.py list
py tune.py fetch-instructions --label v1
py tune.py run --session-id 75
py tune.py run --last 3
py tune.py apply-revised --file reports\session_74_..._revised_instructions.txt --confirm
```

## Outputs

All reports are saved here:

```
sayehboun/tuner/reports/
  session_{id}_{timestamp}.md
  session_{id}_{timestamp}.json
  session_{id}_{timestamp}_revised_instructions.txt
```

Prompt snapshots from `fetch-instructions`:

```
sayehboun/tuner/prompts/history_taker_snapshot_*.txt
```

## Safety

- Never auto-updates production History Taker.
- `apply-revised` requires `--confirm`; production blocked unless `--allow-production`.
