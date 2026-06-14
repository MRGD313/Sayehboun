# Sayehboun Prompt Tuners

Two **separate** Metis evaluator bots — one for history taker tuning, one for judging tuning.

---

## Bot map

| Role | Metis console | `.env` variable | Instructions file |
|------|---------------|-----------------|-------------------|
| History taker (production) | history taker bot | `METIS_BOT_ID` | live on Metis |
| History taker **tuner** | history_taker_tuner | `METIS_HISTORY_TAKER_EVALUATOR_BOT_ID` or `METIS_EVALUATOR_BOT_ID` | `prompts/evaluator_instructions.txt` |
| Judging (production) | judging bot | `METIS_JUDGING_BOT_ID` | `prompts/judging_instructions.txt` |
| Judging **tuner** | judgment_tuner | `METIS_JUDGING_EVALUATOR_BOT_ID` | `prompts/judging_evaluator_instructions.txt` |

Both evaluators: **plain text output** on Metis (no JSON response format).

Verify configuration:

```powershell
py tune.py doctor
py judge_tune.py doctor
```

---

## History Taker (`tune.py`)

```powershell
py tune.py list
py tune.py run --session-id 82
py tune.py apply-revised --file reports\..._revised_prompt.txt --confirm
```

Output: `tuner/reports/session_{id}_..._revised_prompt.txt`

---

## Judging Bot (`judge_tune.py`)

```powershell
py judge_tune.py list
py judge_tune.py run --session-id 81
py judge_tune.py apply-revised --file judging_reports\..._revised_prompt.txt --confirm
```

Output: `tuner/judging_reports/session_{id}_..._revised_prompt.txt`

Requires sessions with both `judgment` and `judgment_to_patient` in DB.

---

## `.env` example

See project root `.env.example` for all tuner variables.

```env
METIS_EVALUATOR_BOT_ID=your-history-taker-tuner-bot-id
METIS_JUDGING_EVALUATOR_BOT_ID=your-judging-tuner-bot-id
```

These **must be different** bot IDs. `run` fails if they match.

---

## Rules

- One DB session per tune (`tuned_sessions.json` / `judging_tuned_sessions.json`); use `--force` to re-run.
- `apply-revised` requires `--confirm`.
- History taker apply to production blocked unless `--allow-production`.
