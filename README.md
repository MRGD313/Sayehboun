# Sayehboun — Persian Clinical Triage Bale Bot

Sayehboun is an open-source [Bale](https://bale.ai) bot for **Persian-language clinical history taking**. It guides patients through a structured 3-phase triage conversation, then formats the history for a doctor via Metis AI bots and SQLite session storage.

## Features

- Bale messenger integration (Persian UI)
- 3-phase history taker via Metis AI (DeepSeek)
- Demographics collection once per user
- Structured history formatter bot for doctor review
- Primary + backup history taker failover
- Prompt versioning and optional **prompt tuner** (`tuner/`) for iterative improvement

## Architecture

```
Patient (Bale) → app.py → Metis history taker (3 phases)
                      → Metis formatter → Doctor (formatted history)
                      → Metis judging bot → Doctor (triage judgment)
                      → SQLite (sessions, demographics, settings)
```

## Requirements

- Python 3.11+
- Bale bot token
- [Metis AI](https://metisai.ir) API key and bot IDs (history taker, backup, formatter, optional evaluator)

## Quick start

1. **Clone and install**

```powershell
git clone <your-repo-url>
cd sayehboun
py -m pip install -r requirements.txt
```

2. **Configure environment**

```powershell
copy .env.example .env
# Edit .env with your tokens and Metis bot IDs
```

3. **Run the bot**

```powershell
py app.py
```

Only one instance should run at a time (enforced via `.bot.instance.lock`).

**Restart (always stops all instances first):**

```powershell
py bot_ctl.py restart
# or
.\restart_bot.ps1
```

```powershell
py bot_ctl.py stop
py bot_ctl.py start
```

## Environment variables

See [`.env.example`](.env.example) for the full list. Main keys:

| Variable | Purpose |
|----------|---------|
| `BALE_BOT_TOKEN` | Bale bot token |
| `DEEPSEEK_API_KEY` | Metis API key |
| `METIS_BOT_ID` | Primary history taker bot |
| `METIS_BOT_ID_BACKUP` | Backup history taker |
| `METIS_STRUCTURE_BOT_ID` | History formatter bot |
| `METIS_JUDGING_BOT_ID` | Judging / triage bot (`sayehboun_judging_bot`) |
| `JUDGING_PROMPT_VERSION` | Judging prompt version (`v1`, …) |
| `FORMATTER_PROMPT_VERSION` | Formatter prompt version (`v1`, …) |
| `DOCTOR_BALE_USERNAME` | Doctor account for notifications |
| `PROMPT_VERSION` | Active prompt version label (`v1`, `v2`, …) |

**VPN / proxy:** Metis API calls always bypass the system HTTP proxy and `HTTP_PROXY` env vars (default). Use `METIS_USE_SYSTEM_PROXY=1` only if you intentionally want Metis through the VPN. Bale polling uses `USE_SYSTEM_PROXY=1` for the same. Full-tunnel VPN apps may still need split-tunnel rules for `api.metisai.ir` in the VPN client.

## Prompt tuner

The optional tuner reads completed sessions from `bot.db`, evaluates them with a Metis evaluator bot, and writes revised prompt drafts. See [`tuner/README.md`](tuner/README.md).

```powershell
cd tuner
py tune.py list
py tune.py run --session-id <id>
py tune.py prompt list
```

Versioned prompts live in `tuner/prompts/versions/`.

## Project layout

```
sayehboun/
├── app.py                 # Bale bot main loop
├── db.py                  # SQLite persistence
├── deepseek_client.py     # Metis history taker client
├── history_formatter.py   # Structured history formatter
├── judging_client.py      # Triage judgment bot for doctor
├── metis_utils.py         # Metis HTTP helpers, retries, logging
├── tuner/                 # Prompt evaluation & versioning
└── requirements.txt
```

## Security notes

- Never commit `.env` or `*.db` — they are gitignored.
- Tuner reports may contain real patient text; `tuner/reports/` is gitignored except `.gitkeep`.
- Rotate any API keys that were ever committed or shared.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

This software is for research and educational purposes. It is **not** a medical device and does not provide diagnosis or treatment. Use under appropriate clinical and legal oversight.
