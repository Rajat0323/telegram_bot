import asyncio
import logging
import os
from contextlib import suppress
from typing import Any

from dotenv import load_dotenv
import httpx
from telegram import Poll
from telegram.ext import Application, CommandHandler, ContextTypes

from content_templates import (
    BrandConfig,
    attach_cta,
    auto_live_update,
    engagement_poll,
    giveaway_post,
    morning_preview,
    result_summary,
    score_update,
    toss_update,
    wicket_alert,
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_CHAT_ID = os.getenv("CHANNEL_CHAT_ID", "")
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID", "")
BRAND_HANDLE = os.getenv("BRAND_HANDLE", "@yourgroup")
DEFAULT_HASHTAGS = os.getenv("DEFAULT_HASHTAGS", "#IPL2026 #LiveScore #FantasyTips")
CRICKET_API_KEY = os.getenv("CRICKET_API_KEY", "")
CRICKET_API_BASE_URL = os.getenv("CRICKET_API_BASE_URL", "https://api.cricapi.com/v1/currentMatches")
TOURNAMENT_KEYWORDS = [
    keyword.strip().lower()
    for keyword in os.getenv("TOURNAMENT_KEYWORDS", "ipl,indian premier league").split(",")
    if keyword.strip()
]
AUTO_UPDATE_ENABLED = os.getenv("AUTO_UPDATE_ENABLED", "true").lower() == "true"
AUTO_UPDATE_INTERVAL_SECONDS = int(os.getenv("AUTO_UPDATE_INTERVAL_SECONDS", "60"))
MIN_SAFE_INTERVAL_SECONDS = 900

brand = BrandConfig(handle=BRAND_HANDLE, hashtags=DEFAULT_HASHTAGS)


async def send_to_channel(context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    if not CHANNEL_CHAT_ID:
        raise ValueError("CHANNEL_CHAT_ID is not configured")
    await context.bot.send_message(chat_id=CHANNEL_CHAT_ID, text=attach_cta(message, brand))


async def send_to_group(context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    if not GROUP_CHAT_ID:
        raise ValueError("GROUP_CHAT_ID is not configured")
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=attach_cta(message, brand))


async def send_to_targets(context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    if CHANNEL_CHAT_ID:
        await send_to_channel(context, message)
    if GROUP_CHAT_ID:
        await send_to_group(context, message)


def match_contains_keyword(match: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(match.get(field, ""))
        for field in ("name", "series", "series_id", "matchType", "status", "venue")
    ).lower()
    return any(keyword in haystack for keyword in TOURNAMENT_KEYWORDS)


def extract_teams(match: dict[str, Any]) -> tuple[str, str]:
    team_info = match.get("teamInfo") or []
    if len(team_info) >= 2:
        return str(team_info[0].get("name", "Team A")), str(team_info[1].get("name", "Team B"))

    teams = match.get("teams") or []
    if len(teams) >= 2:
        return str(teams[0]), str(teams[1])

    return "Team A", "Team B"


def format_score_lines(match: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for item in match.get("score") or []:
        innings = str(item.get("inning", "Innings"))
        runs = item.get("r")
        wickets = item.get("w")
        overs = item.get("o")
        if runs is None or wickets is None or overs is None:
            continue
        lines.append(f"{innings}: {runs}/{wickets} ({overs} ov)")
    return lines


def build_match_digest(match: dict[str, Any]) -> str:
    status = str(match.get("status", ""))
    scores = " | ".join(format_score_lines(match))
    toss = str(match.get("tossWinner", ""))
    return f"{status}::{scores}::{toss}"


async def fetch_current_matches() -> list[dict[str, Any]]:
    if not CRICKET_API_KEY:
        return []

    params = {"apikey": CRICKET_API_KEY, "offset": 0}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(CRICKET_API_BASE_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    if isinstance(payload, dict):
        return payload.get("data") or []
    return []


async def auto_live_loop(application: Application) -> None:
    interval = max(AUTO_UPDATE_INTERVAL_SECONDS, MIN_SAFE_INTERVAL_SECONDS)
    while True:
        try:
            if application.bot_data.get("autolive_enabled", AUTO_UPDATE_ENABLED):
                await process_live_updates(application)
        except Exception:
            logging.exception("Auto live update loop failed")
        await asyncio.sleep(interval)


async def process_live_updates(application: Application) -> None:
    matches = await fetch_current_matches()
    live_state = application.bot_data.setdefault("live_state", {})

    for match in matches:
        if not match_contains_keyword(match):
            continue

        match_id = str(match.get("id") or match.get("unique_id") or match.get("name"))
        if not match_id:
            continue

        digest = build_match_digest(match)
        if live_state.get(match_id) == digest:
            continue

        live_state[match_id] = digest
        team_a, team_b = extract_teams(match)
        status = str(match.get("status", "Live"))
        score_lines = format_score_lines(match)

        title = "Live IPL Update"
        if "won" in status.lower():
            title = "Match Result"
        elif "toss" in status.lower():
            title = "Toss Update"

        message = auto_live_update(title, team_a, team_b, status, score_lines)
        fake_context = type("Context", (), {"bot": application.bot})()
        await send_to_targets(fake_context, message)


async def post_init(application: Application) -> None:
    application.bot_data["autolive_enabled"] = AUTO_UPDATE_ENABLED
    application.bot_data["live_state"] = {}
    if CRICKET_API_KEY:
        application.bot_data["live_task"] = asyncio.create_task(auto_live_loop(application))
        logging.info("Auto live updater enabled")
    else:
        logging.info("Auto live updater skipped because CRICKET_API_KEY is missing")


async def post_shutdown(application: Application) -> None:
    live_task = application.bot_data.get("live_task")
    if live_task:
        live_task.cancel()
        with suppress(asyncio.CancelledError):
            await live_task


async def start_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Bot ready hai. /help use karo aur templates se fast updates bhejo."
    )


async def help_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "/preview team_a team_b venue pitch captain vice_captain\n"
        "/poll team_a team_b\n"
        "/toss text\n"
        "/score team score overs\n"
        "/wicket player team_score overs\n"
        "/result result_text | player_of_match\n"
        "/giveaway amount\n"
        "/post text\n"
        "/live_on\n"
        "/live_off\n"
        "/live_status"
    )
    await update.message.reply_text(help_text)


async def preview_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 6:
        await update.message.reply_text("Usage: /preview team_a team_b venue pitch captain vice_captain")
        return

    team_a, team_b, venue, pitch, captain, vice_captain = context.args[:6]
    message = morning_preview(team_a, team_b, venue, pitch, captain, vice_captain)
    await send_to_channel(context, message)
    await update.message.reply_text("Morning preview post ho gaya.")


async def poll_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /poll team_a team_b")
        return

    team_a, team_b = context.args[:2]
    poll_text = engagement_poll(team_a, team_b)

    if not GROUP_CHAT_ID:
        await update.message.reply_text("GROUP_CHAT_ID configure nahi hai.")
        return

    await context.bot.send_poll(
        chat_id=GROUP_CHAT_ID,
        question="Aaj ka match kaun jeetega?",
        options=[team_a, team_b],
        is_anonymous=False,
        type=Poll.REGULAR,
    )
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=attach_cta(poll_text, brand))
    await update.message.reply_text("Poll group me bhej diya.")


async def toss_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /toss text")
        return

    message = toss_update(" ".join(context.args))
    await send_to_targets(context, message)
    await update.message.reply_text("Toss update bhej diya.")


async def score_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /score team score overs")
        return

    team, score, overs = context.args[:3]
    message = score_update(team, score, overs)
    await send_to_targets(context, message)
    await update.message.reply_text("Score update bhej diya.")


async def wicket_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /wicket player team_score overs")
        return

    player = context.args[0]
    team_score = context.args[1]
    overs = context.args[2]
    message = wicket_alert(f"{player} OUT", team_score, overs)
    await send_to_targets(context, message)
    await update.message.reply_text("Wicket alert bhej diya.")


async def result_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    joined = " ".join(context.args)
    if "|" not in joined:
        await update.message.reply_text("Usage: /result result_text | player_of_match")
        return

    result_text, player_of_match = [part.strip() for part in joined.split("|", 1)]
    message = result_summary(result_text, player_of_match)
    await send_to_targets(context, message)
    await update.message.reply_text("Result summary bhej diya.")


async def giveaway_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /giveaway amount")
        return

    message = giveaway_post(context.args[0])
    await send_to_targets(context, message)
    await update.message.reply_text("Giveaway post bhej diya.")


async def post_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /post text")
        return

    await send_to_targets(context, " ".join(context.args))
    await update.message.reply_text("Custom post channel aur group me chala gaya.")


async def live_on_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not CRICKET_API_KEY:
        await update.message.reply_text("CRICKET_API_KEY missing hai. Pehle API key set karo.")
        return

    context.application.bot_data["autolive_enabled"] = True
    await update.message.reply_text("Automatic IPL live updates ON ho gaye.")


async def live_off_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.application.bot_data["autolive_enabled"] = False
    await update.message.reply_text("Automatic IPL live updates OFF ho gaye.")


async def live_status_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    enabled = context.application.bot_data.get("autolive_enabled", AUTO_UPDATE_ENABLED)
    key_status = "set" if CRICKET_API_KEY else "missing"
    effective_interval = max(AUTO_UPDATE_INTERVAL_SECONDS, MIN_SAFE_INTERVAL_SECONDS)
    approx_calls = 86400 // effective_interval
    await update.message.reply_text(
        f"Auto live: {'ON' if enabled else 'OFF'}\n"
        f"API key: {key_status}\n"
        f"Interval: {effective_interval}s\n"
        f"Approx API calls/day: {approx_calls}\n"
        f"Tournaments: {', '.join(TOURNAMENT_KEYWORDS)}"
    )


def main() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not configured")

    # Python 3.14 no longer creates a default event loop automatically.
    asyncio.set_event_loop(asyncio.new_event_loop())

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("preview", preview_command))
    application.add_handler(CommandHandler("poll", poll_command))
    application.add_handler(CommandHandler("toss", toss_command))
    application.add_handler(CommandHandler("score", score_command))
    application.add_handler(CommandHandler("wicket", wicket_command))
    application.add_handler(CommandHandler("result", result_command))
    application.add_handler(CommandHandler("giveaway", giveaway_command))
    application.add_handler(CommandHandler("post", post_command))
    application.add_handler(CommandHandler("live_on", live_on_command))
    application.add_handler(CommandHandler("live_off", live_off_command))
    application.add_handler(CommandHandler("live_status", live_status_command))

    logging.info("Bot started")
    application.run_polling()


if __name__ == "__main__":
    main()
