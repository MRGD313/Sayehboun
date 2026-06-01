# History Taker Prompt Tuner

Lives inside the Sayehboun project at `tuner/`. Reads **real sessions** from `bot.db`, calls the Metis **evaluator bot**, and writes reports with a **full revised History Taker prompt** for manual review.

Uses the main project `.env` at `sayehboun/.env` (same keys as the Bale bot + `METIS_EVALUATOR_BOT_ID`).

## Setup

Already uses root `requirements.txt` (`metisai`, `httpx`, `python-dotenv`).

Add to **`sayehboun/.env`** (project root):

```env
METIS_EVALUATOR_BOT_ID=your-evaluator-bot-id
PROMPT_VERSION=v1
```

`METIS_BOT_ID` / `DEEPSEEK_API_KEY` / `SQLITE_DB_PATH` are reused automatically.

Evaluator bot instructions: paste `tuner/prompts/evaluator_instructions.txt` into Metis.

**Metis tip:** Leave **summarizer** empty on the evaluator bot (same as history taker).

## Prompt versioning

Stored under `tuner/prompts/versions/`:

| File | Purpose |
|------|---------|
| `history_taker_v1.txt` | Original prompt |
| `history_taker_v2.txt` | Tuner iteration from session 74 |
| `manifest.json` | Version metadata + which is **current** |
| `CURRENT` | Quick marker (`v1`) |

**Current version: `v1`** (also in root `.env` as `PROMPT_VERSION=v1`).

When you change the Metis prompt, register a new version:

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
