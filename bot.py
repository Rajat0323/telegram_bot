import asyncio
import html
import logging
import os
import re
from contextlib import suppress
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

from dotenv import load_dotenv
import httpx
from telegram import ChatMemberUpdated, Poll
from telegram.ext import Application, ChatMemberHandler, CommandHandler, ContextTypes

from content_templates import (
    BrandConfig,
    attach_cta,
    auto_live_update,
    cricket_news_caption,
    debate_post,
    engagement_poll,
    giveaway_post,
    morning_preview,
    points_table_impact,
    result_summary,
    score_update,
    toss_update,
    trivia_question,
    welcome_message,
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

IST = timezone(timedelta(hours=5, minutes=30))

brand = BrandConfig(handle=BRAND_HANDLE, hashtags=DEFAULT_HASHTAGS)


def ist_now() -> datetime:
    return datetime.now(UTC).astimezone(IST)


def match_is_today_or_tomorrow(match_time: datetime) -> bool:
    now_ist = ist_now()
    match_ist = match_time.astimezone(IST)
    today = now_ist.date()
    match_date = match_ist.date()
    return match_date == today or match_date == (today + timedelta(days=1))


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


def parse_match_time(match: dict[str, Any]) -> datetime | None:
    raw = match.get("dateTimeGMT")
    if not raw:
        return None
    with suppress(ValueError):
        return datetime.fromisoformat(str(raw)).replace(tzinfo=UTC)
    return None


NEWS_RSS_URL = os.getenv(
    "NEWS_RSS_URL",
    "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en",
)


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


def strip_html_tags(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_image_from_html(raw: str) -> str | None:
    patterns = [
        r'<img[^>]+src="([^"]+)"',
        r"<img[^>]+src='([^']+)'",
        r"https://[^\s\"']+\.(?:jpg|jpeg|png|webp)(?:\?[^\s\"']*)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, flags=re.IGNORECASE)
        if match:
            url = html.unescape(match.group(1) if "(" in pattern else match.group(0))
            if url.startswith("http"):
                return url
    return None


async def fetch_cricket_news(query: str = "IPL 2026 cricket", limit: int = 3) -> list[dict[str, str]]:
    rss_url = NEWS_RSS_URL
    if "{query}" in rss_url:
        rss_url = rss_url.format(query=quote_plus(query))
    items: list[dict[str, str]] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(rss_url, follow_redirects=True)
            response.raise_for_status()
        root = ElementTree.fromstring(response.text)
        for item in root.findall(".//item"):
            title = item.findtext("title", default="").strip()
            link = item.findtext("link", default="").strip()
            guid = item.findtext("guid", default=link).strip()
            raw_desc = item.findtext("description", default="").strip()
            description = strip_html_tags(raw_desc)
            source = item.findtext("source", default="Cricket News").strip()
            image_url = extract_image_from_html(raw_desc)
            if not title or not link:
                continue
            items.append({
                "id": guid,
                "title": title,
                "description": description[:200] + "..." if len(description) > 200 else description,
                "source": source or "Cricket News",
                "link": link,
                "image_url": image_url or "",
            })
            if len(items) >= limit:
                break
    except Exception:
        logging.exception("Failed to fetch cricket news")
    return items


async def send_photo_to_all(bot: Any, image_url: str, caption: str) -> None:
    full_caption = attach_cta(caption, brand)
    for chat_id in [CHANNEL_CHAT_ID, GROUP_CHAT_ID]:
        if not chat_id:
            continue
        try:
            await bot.send_photo(chat_id=chat_id, photo=image_url, caption=full_caption)
        except Exception:
            try:
                await bot.send_message(chat_id=chat_id, text=full_caption)
            except Exception:
                logging.exception("Failed to send message to %s", chat_id)
        await asyncio.sleep(1)


async def send_text_to_all(bot: Any, message: str) -> None:
    full_message = attach_cta(message, brand)
    for chat_id in [CHANNEL_CHAT_ID, GROUP_CHAT_ID]:
        if not chat_id:
            continue
        try:
            await bot.send_message(chat_id=chat_id, text=full_message)
        except Exception:
            logging.exception("Failed to send message to %s", chat_id)
        await asyncio.sleep(1)


def has_live_ipl_match(matches: list[dict[str, Any]]) -> bool:
    for match in matches:
        if not match_contains_keyword(match):
            continue
        started = bool(match.get("matchStarted"))
        ended = bool(match.get("matchEnded"))
        if started and not ended:
            return True
    return False


async def no_match_news_loop(application: Application) -> None:
    interval = max(AUTO_UPDATE_INTERVAL_SECONDS, MIN_SAFE_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(interval)
        try:
            matches = await fetch_current_matches()
            if has_live_ipl_match(matches):
                logging.info("Live match in progress — skipping news loop")
                continue

            posted_news: set[str] = application.bot_data.setdefault("posted_news_ids", set())
            news_items = await fetch_cricket_news("IPL 2026 cricket latest news", limit=1)

            for item in news_items:
                if item["id"] in posted_news:
                    continue
                posted_news.add(item["id"])
                caption = cricket_news_caption(
                    item["title"], item["description"], item["source"], item["link"]
                )
                if item.get("image_url"):
                    await send_photo_to_all(application.bot, item["image_url"], caption)
                else:
                    await send_text_to_all(application.bot, caption)
                logging.info("News posted (no live match): %s", item["title"])
                break

        except Exception:
            logging.exception("No-match news loop failed")


async def trivia_loop(application: Application) -> None:
    trivia_interval = 2 * 3600
    await asyncio.sleep(1800)
    while True:
        try:
            matches = await fetch_current_matches()
            if not has_live_ipl_match(matches) and GROUP_CHAT_ID:
                question, options = trivia_question()
                await application.bot.send_poll(
                    chat_id=GROUP_CHAT_ID,
                    question=question,
                    options=options,
                    is_anonymous=False,
                    type=Poll.REGULAR,
                )
                logging.info("Trivia poll posted")
        except Exception:
            logging.exception("Trivia loop failed")
        await asyncio.sleep(trivia_interval)


async def auto_live_loop(application: Application) -> None:
    interval = max(AUTO_UPDATE_INTERVAL_SECONDS, MIN_SAFE_INTERVAL_SECONDS)
    while True:
        try:
            if application.bot_data.get("autolive_enabled", AUTO_UPDATE_ENABLED):
                await process_live_updates(application)
        except Exception:
            logging.exception("Auto live update loop failed")
        await asyncio.sleep(interval)


async def debate_loop(application: Application) -> None:
    debate_interval = 4 * 3600
    while True:
        await asyncio.sleep(debate_interval)
        try:
            if not GROUP_CHAT_ID:
                continue
            matches = await fetch_current_matches()
            ipl_matches = [m for m in matches if match_contains_keyword(m)]
            target_match = None
            for match in ipl_matches:
                mt = parse_match_time(match)
                if mt and match_is_today_or_tomorrow(mt):
                    target_match = match
                    break
            if not target_match and ipl_matches:
                target_match = ipl_matches[0]
            if not target_match:
                continue
            team_a, team_b = extract_teams(target_match)
            question, options = debate_post(team_a, team_b)
            await application.bot.send_poll(
                chat_id=GROUP_CHAT_ID,
                question=question,
                options=options,
                is_anonymous=False,
                type=Poll.REGULAR,
            )
            logging.info("Debate poll posted to group")
        except Exception:
            logging.exception("Debate loop failed")


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

        previous_digest = live_state.get(match_id, "")
        live_state[match_id] = digest
        team_a, team_b = extract_teams(match)
        status = str(match.get("status", "Live"))
        score_lines = format_score_lines(match)

        title = "Live IPL Update"
        is_wicket = False
        is_result = False

        if "won" in status.lower():
            title = "Match Result"
            is_result = True
        elif "toss" in status.lower():
            title = "Toss Update"
        elif previous_digest:
            prev_scores = previous_digest.split("::")
            if len(prev_scores) > 1:
                import re
                prev_w_match = re.search(r"/(\d+)", prev_scores[1])
                for line in score_lines:
                    curr_w_match = re.search(r"/(\d+)", line)
                    if prev_w_match and curr_w_match:
                        if int(curr_w_match.group(1)) > int(prev_w_match.group(1)):
                            title = "WICKET ALERT!"
                            is_wicket = True
                            break

        fake_context = type("Context", (), {"bot": application.bot})()
        message = auto_live_update(title, team_a, team_b, status, score_lines)
        await send_to_targets(fake_context, message)

        if is_result:
            winner = team_a if team_a.lower() in status.lower() else team_b
            loser = team_b if winner == team_a else team_a
            match_name = str(match.get("name", f"{team_a} vs {team_b}"))
            impact_msg = points_table_impact(winner, loser, match_name)
            await send_to_targets(fake_context, impact_msg)

            if GROUP_CHAT_ID:
                question, options = debate_post(team_a, team_b)
                await application.bot.send_poll(
                    chat_id=GROUP_CHAT_ID,
                    question=f"Post-match: {question}",
                    options=options,
                    is_anonymous=False,
                    type=Poll.REGULAR,
                )


async def post_init(application: Application) -> None:
    application.bot_data["autolive_enabled"] = AUTO_UPDATE_ENABLED
    application.bot_data["live_state"] = {}
    application.bot_data["welcomed_users"] = set()
    application.bot_data["posted_news_ids"] = set()
    if CRICKET_API_KEY:
        application.bot_data["live_task"] = asyncio.create_task(auto_live_loop(application))
        application.bot_data["debate_task"] = asyncio.create_task(debate_loop(application))
        application.bot_data["news_task"] = asyncio.create_task(no_match_news_loop(application))
        application.bot_data["trivia_task"] = asyncio.create_task(trivia_loop(application))
        logging.info("Auto live updater, news loop, and trivia loop enabled")
    else:
        application.bot_data["news_task"] = asyncio.create_task(no_match_news_loop(application))
        application.bot_data["trivia_task"] = asyncio.create_task(trivia_loop(application))
        logging.info("Live updater skipped (no API key) — news and trivia loops running")


async def post_shutdown(application: Application) -> None:
    for key in ("live_task", "debate_task", "news_task", "trivia_task"):
        task = application.bot_data.get(key)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


async def welcome_new_member(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    result: ChatMemberUpdated = update.chat_member
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    if old_status in ("left", "kicked") and new_status == "member":
        user = result.new_chat_member.user
        if user.is_bot:
            return
        welcomed = context.application.bot_data.get("welcomed_users", set())
        if user.id in welcomed:
            return
        welcomed.add(user.id)
        context.application.bot_data["welcomed_users"] = welcomed
        name = user.first_name or "Friend"
        msg = welcome_message(name, brand)
        await context.bot.send_message(chat_id=result.chat.id, text=msg)
        logging.info("Welcome message sent to %s", name)


async def start_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Bot ready hai! /help use karo aur templates se fast updates bhejo."
    )


async def help_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "Available commands:\n\n"
        "/preview team_a team_b venue pitch captain vice_captain\n"
        "/poll team_a team_b\n"
        "/debate team_a team_b\n"
        "/toss text\n"
        "/score team score overs\n"
        "/wicket player team_score overs\n"
        "/result result_text | player_of_match\n"
        "/points winner loser match_name\n"
        "/giveaway amount\n"
        "/post text\n"
        "/countdown\n"
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


async def debate_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /debate team_a team_b")
        return

    if not GROUP_CHAT_ID:
        await update.message.reply_text("GROUP_CHAT_ID configure nahi hai.")
        return

    team_a, team_b = context.args[:2]
    question, options = debate_post(team_a, team_b)
    await context.bot.send_poll(
        chat_id=GROUP_CHAT_ID,
        question=question,
        options=options,
        is_anonymous=False,
        type=Poll.REGULAR,
    )
    await update.message.reply_text("Debate poll group me bhej diya.")


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


async def points_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /points winner loser match_name")
        return

    winner = context.args[0]
    loser = context.args[1]
    match_name = " ".join(context.args[2:])
    message = points_table_impact(winner, loser, match_name)
    await send_to_targets(context, message)
    await update.message.reply_text("Points table update bhej diya.")


async def countdown_command(update, context: ContextTypes.DEFAULT_TYPE) -> None:
    matches = await fetch_current_matches()
    now = datetime.now(UTC)
    target = None
    for match in matches:
        if not match_contains_keyword(match):
            continue
        mt = parse_match_time(match)
        if mt and mt > now and match_is_today_or_tomorrow(mt):
            target = match
            break

    if not target:
        await update.message.reply_text("Aaj ya kal ka koi IPL match schedule nahi mila.")
        return

    team_a, team_b = extract_teams(target)
    mt = parse_match_time(target)
    ist_time = mt.astimezone(IST)
    remaining = mt - now
    hours = int(remaining.total_seconds() // 3600)
    minutes = int((remaining.total_seconds() % 3600) // 60)
    date_str = ist_time.strftime("%d %b %Y, %I:%M %p IST")
    countdown_text = f"Starts in {hours}h {minutes}m" if hours > 0 else f"Starts in {minutes} min"
    venue = str(target.get("venue", "Venue TBA"))
    status = str(target.get("status", "Upcoming"))

    from content_templates import styled_countdown_message
    msg = styled_countdown_message(team_a, team_b, venue, countdown_text, status, date_str)
    await send_to_targets(context, msg)
    await update.message.reply_text("Countdown bhej diya.")


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

    asyncio.set_event_loop(asyncio.new_event_loop())

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("preview", preview_command))
    application.add_handler(CommandHandler("poll", poll_command))
    application.add_handler(CommandHandler("debate", debate_command))
    application.add_handler(CommandHandler("toss", toss_command))
    application.add_handler(CommandHandler("score", score_command))
    application.add_handler(CommandHandler("wicket", wicket_command))
    application.add_handler(CommandHandler("result", result_command))
    application.add_handler(CommandHandler("points", points_command))
    application.add_handler(CommandHandler("countdown", countdown_command))
    application.add_handler(CommandHandler("giveaway", giveaway_command))
    application.add_handler(CommandHandler("post", post_command))
    application.add_handler(CommandHandler("live_on", live_on_command))
    application.add_handler(CommandHandler("live_off", live_off_command))
    application.add_handler(CommandHandler("live_status", live_status_command))

    logging.info("Bot started")
    application.run_polling(allowed_updates=["message", "chat_member"])


if __name__ == "__main__":
    main()
