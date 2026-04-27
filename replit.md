# Telegram IPL Growth Kit

A Python-based Telegram bot that posts IPL live cricket updates, fantasy tips, polls, and viral CTAs to a Telegram channel and/or group.

## Project Structure

- `bot.py` — Main Telegram bot with command handlers and auto-live update loop
- `autoposter.py` — One-shot auto poster for scheduled/external automation
- `content_templates.py` — Message formatting templates and brand config
- `get_chat_id.py` — Helper to retrieve Telegram chat IDs
- `register_task.ps1` — Windows Task Scheduler registration (for local Windows use)
- `requirements.txt` — Python dependencies
- `.env.example` — Template for required environment variables

## Tech Stack

- **Language**: Python 3.12
- **Bot Framework**: python-telegram-bot 21.10
- **HTTP Client**: httpx 0.28.1
- **Env Management**: python-dotenv

## Workflow

- **Start application**: `python bot.py` (console output)
  - Fails gracefully if `BOT_TOKEN` is not set

## Required Environment Variables

Set these as Replit Secrets before running:

| Variable | Description |
|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather |
| `CHANNEL_CHAT_ID` | Channel username or chat ID (e.g. `@mychannel`) |
| `GROUP_CHAT_ID` | Group username or chat ID (e.g. `@mygroup`) |
| `BRAND_HANDLE` | Your group/channel handle for CTAs |
| `DEFAULT_HASHTAGS` | Hashtags appended to posts |
| `CRICKET_API_KEY` | API key from cricapi.com for live scores |
| `CRICKET_API_BASE_URL` | CricAPI endpoint (default set) |
| `TOURNAMENT_KEYWORDS` | Comma-separated keywords to filter matches |
| `NEWS_RSS_URL` | Google News RSS for IPL news |
| `ENABLE_NEWS_IMAGES` | `true`/`false` |
| `AUTO_UPDATE_ENABLED` | `true`/`false` |
| `AUTO_UPDATE_INTERVAL_SECONDS` | Polling interval (min 900 to stay within 100 calls/day) |

## Bot Commands

- `/start` — Greeting
- `/help` — Show command list
- `/preview team_a team_b venue pitch captain vice_captain` — Morning preview post
- `/poll team_a team_b` — Send match winner poll to group
- `/toss text` — Send toss update
- `/score team score overs` — Send score update
- `/wicket player team_score overs` — Send wicket alert
- `/result result_text | player_of_match` — Send result summary
- `/giveaway amount` — Send giveaway post
- `/post text` — Send custom post
- `/live_on` — Enable auto live updates
- `/live_off` — Disable auto live updates
- `/live_status` — Check auto live update status

## Notes

- Auto live updates poll CricAPI every 15 minutes (900s) to stay within 100 calls/day quota
- Bot deduplicates updates using in-memory state — restarts reset dedup state
- `autoposter.py` is designed for external schedulers (cron, Windows Task Scheduler)
