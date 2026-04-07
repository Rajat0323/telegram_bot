import asyncio
import contextlib
import html
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

import httpx
from dotenv import load_dotenv

from content_templates import BrandConfig, attach_cta, auto_live_update


BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
STATE_FILE = STATE_DIR / "autoposter_state.json"

load_dotenv()


def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


BOT_TOKEN = env_str("BOT_TOKEN", "")
CHANNEL_CHAT_ID = env_str("CHANNEL_CHAT_ID", "")
GROUP_CHAT_ID = env_str("GROUP_CHAT_ID", "")
BRAND_HANDLE = env_str("BRAND_HANDLE", "@yourgroup")
DEFAULT_HASHTAGS = env_str("DEFAULT_HASHTAGS", "#IPL2026 #LiveScore #FantasyTips")
CRICKET_API_KEY = env_str("CRICKET_API_KEY", "")
CRICKET_API_BASE_URL = env_str("CRICKET_API_BASE_URL", "https://api.cricapi.com/v1/currentMatches")
TOURNAMENT_KEYWORDS = [
    keyword.strip().lower()
    for keyword in env_str("TOURNAMENT_KEYWORDS", "ipl,indian premier league").split(",")
    if keyword.strip()
]
NEWS_RSS_URL = env_str(
    "NEWS_RSS_URL",
    "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en",
)
MAX_NEWS_POSTS_PER_RUN = env_int("MAX_NEWS_POSTS_PER_RUN", 1)
TELEGRAM_POST_DELAY_SECONDS = env_float("TELEGRAM_POST_DELAY_SECONDS", 1.5)
COUNTDOWN_BUCKET_MINUTES = env_int("COUNTDOWN_BUCKET_MINUTES", 60)
NEWS_SUMMARY_WORD_LIMIT = env_int("NEWS_SUMMARY_WORD_LIMIT", 60)

brand = BrandConfig(handle=BRAND_HANDLE, hashtags=DEFAULT_HASHTAGS)


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict[str, Any]:
    ensure_state_dir()
    if not STATE_FILE.exists():
        return {"matches": {}, "news": {}, "polls": {}, "next_match": {}}

    with STATE_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_state(state: dict[str, Any]) -> None:
    ensure_state_dir()
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=True, indent=2)


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
    started = str(match.get("matchStarted", ""))
    ended = str(match.get("matchEnded", ""))
    return f"{status}::{scores}::{toss}::{started}::{ended}"


def parse_match_time(match: dict[str, Any]) -> datetime | None:
    raw = match.get("dateTimeGMT")
    if not raw:
        return None

    try:
        return datetime.fromisoformat(str(raw)).replace(tzinfo=UTC)
    except ValueError:
        return None


def is_live_match(match: dict[str, Any]) -> bool:
    return bool(match.get("matchStarted")) and not bool(match.get("matchEnded"))


def is_upcoming_match(match: dict[str, Any], now: datetime) -> bool:
    if bool(match.get("matchStarted")):
        return False
    match_time = parse_match_time(match)
    return match_time is None or match_time >= now


def is_recent_result(match: dict[str, Any], now: datetime) -> bool:
    if not bool(match.get("matchEnded")):
        return False
    match_time = parse_match_time(match)
    if match_time is None:
        return False
    return (now - match_time).total_seconds() <= 18 * 3600


def select_live_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [match for match in matches if match_contains_keyword(match) and is_live_match(match)]


def select_next_match(matches: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    upcoming = [match for match in matches if match_contains_keyword(match) and is_upcoming_match(match, now)]
    if not upcoming:
        return None

    return min(
        upcoming,
        key=lambda item: parse_match_time(item) or datetime.max.replace(tzinfo=UTC),
    )


def describe_countdown(match_time: datetime, now: datetime) -> tuple[str, int]:
    remaining_minutes = max(0, int((match_time - now).total_seconds() // 60))
    bucket = remaining_minutes // max(1, COUNTDOWN_BUCKET_MINUTES)

    if remaining_minutes >= 1440:
        return f"Starts in {remaining_minutes // 1440} day(s)", bucket
    if remaining_minutes >= 60:
        return f"Starts in {remaining_minutes // 60} hour(s) {remaining_minutes % 60} min", bucket
    return f"Starts in {remaining_minutes} min", bucket


def get_primary_score(match: dict[str, Any]) -> dict[str, Any] | None:
    score_items = match.get("score") or []
    if not score_items:
        return None
    return score_items[-1]


def detect_live_event(previous_digest: str | None, match: dict[str, Any]) -> str:
    score = get_primary_score(match)
    if not score or not previous_digest:
        return "Live IPL Update"

    previous_runs = previous_wickets = None
    with contextlib.suppress(Exception):
        previous_scores = previous_digest.split("::")[1]
        last_part = previous_scores.split(" | ")[-1]
        score_part = last_part.split(": ", 1)[1]
        runs_wickets = score_part.split(" ", 1)[0]
        previous_runs, previous_wickets = [int(x) for x in runs_wickets.split("/")]

    runs = score.get("r")
    wickets = score.get("w")
    if previous_wickets is not None and wickets is not None and wickets > previous_wickets:
        return "Wicket Alert"

    if previous_runs is not None and runs is not None:
        for milestone in (50, 100, 150, 200):
            if previous_runs < milestone <= runs:
                return f"{milestone} Up"
        if runs - previous_runs >= 6:
            return "Big Over Update"

    return "Live IPL Update"


def build_next_match_message(match: dict[str, Any], now: datetime) -> tuple[str, str]:
    team_a, team_b = extract_teams(match)
    match_time = parse_match_time(match)
    status = str(match.get("status", "Upcoming"))
    venue = str(match.get("venue", "Venue update soon"))
    if match_time is None:
        countdown_text = "Schedule update soon"
        bucket_key = f"{match.get('id', 'unknown')}:unknown"
    else:
        countdown_text, bucket = describe_countdown(match_time, now)
        bucket_key = f"{match.get('id', 'unknown')}:{bucket}"

    message = (
        "Next IPL Match\n"
        f"{team_a} vs {team_b}\n"
        f"Venue: {venue}\n"
        f"{countdown_text}\n"
        f"Status: {status}"
    )
    return message, bucket_key


def build_news_query(matches: list[dict[str, Any]], next_match: dict[str, Any] | None) -> str:
    keywords = ["IPL"]
    for match in matches[:2]:
        team_a, team_b = extract_teams(match)
        keywords.extend([team_a, team_b])

    if next_match:
        team_a, team_b = extract_teams(next_match)
        keywords.extend([team_a, team_b, "preview"])

    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        normalized = keyword.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(keyword)

    return " OR ".join(deduped[:6])


def build_news_url(query: str) -> str:
    if "{query}" in NEWS_RSS_URL:
        return NEWS_RSS_URL.format(query=quote_plus(query))
    return NEWS_RSS_URL


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def summarize_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]).rstrip(" ,.") + "..."


def title_matches_context(title: str, live_matches: list[dict[str, Any]], next_match: dict[str, Any] | None) -> bool:
    lowered = title.lower()
    if "ipl" not in lowered and "indian premier league" not in lowered:
        return False

    for match in live_matches:
        team_a, team_b = extract_teams(match)
        if team_a.lower() in lowered or team_b.lower() in lowered:
            return True

    if next_match:
        team_a, team_b = extract_teams(next_match)
        return team_a.lower() in lowered or team_b.lower() in lowered or "preview" in lowered

    return True


async def post_message(client: httpx.AsyncClient, chat_id: str, message: str) -> None:
    if not chat_id:
        return

    payload = {"chat_id": chat_id, "text": attach_cta(message, brand)}
    response: httpx.Response | None = None

    for _ in range(3):
        response = await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
        )

        if response.status_code != 429:
            response.raise_for_status()
            await asyncio.sleep(TELEGRAM_POST_DELAY_SECONDS)
            return

        retry_after = 5
        with contextlib.suppress(Exception):
            data = response.json()
            retry_after = int(data.get("parameters", {}).get("retry_after", retry_after))
        await asyncio.sleep(retry_after + 1)

    if response is not None:
        response.raise_for_status()


async def post_to_targets(client: httpx.AsyncClient, message: str) -> None:
    await post_message(client, CHANNEL_CHAT_ID, message)
    await post_message(client, GROUP_CHAT_ID, message)


async def send_daily_poll(client: httpx.AsyncClient, team_a: str, team_b: str) -> None:
    if not GROUP_CHAT_ID:
        return

    response = await client.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPoll",
        json={
            "chat_id": GROUP_CHAT_ID,
            "question": "Aaj ka IPL match kaun jeetega?",
            "options": [team_a, team_b],
            "is_anonymous": False,
        },
    )
    response.raise_for_status()
    await asyncio.sleep(TELEGRAM_POST_DELAY_SECONDS)


async def fetch_current_matches(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    if not CRICKET_API_KEY:
        return []

    response = await client.get(
        CRICKET_API_BASE_URL,
        params={"apikey": CRICKET_API_KEY, "offset": 0},
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        return payload.get("data") or []
    return []


async def fetch_news_items(
    client: httpx.AsyncClient,
    live_matches: list[dict[str, Any]],
    next_match: dict[str, Any] | None,
) -> list[dict[str, str]]:
    query = build_news_query(live_matches, next_match)
    response = await client.get(build_news_url(query))
    response.raise_for_status()
    root = ElementTree.fromstring(response.text)

    items: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="").strip()
        link = item.findtext("link", default="").strip()
        guid = item.findtext("guid", default=link).strip()
        description = strip_html(item.findtext("description", default="").strip())
        source = item.findtext("source", default="").strip()
        if not title or not link:
            continue
        if not title_matches_context(title, live_matches, next_match):
            continue
        items.append(
            {
                "id": guid,
                "title": title,
                "link": link,
                "description": description,
                "source": source,
            }
        )
        if len(items) >= MAX_NEWS_POSTS_PER_RUN:
            break
    return items


def build_news_message(title: str, description: str, link: str, source: str) -> str:
    body = description or title
    summary_seed = f"{title}. {body}".strip()
    summary = summarize_words(summary_seed, NEWS_SUMMARY_WORD_LIMIT)
    source_line = f"Source: {source}" if source else "Source: News feed"
    return (
        "IPL Match News\n"
        f"{summary}\n"
        f"{source_line}\n"
        f"Read more: {link}"
    )


async def run_once() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not configured")

    state = load_state()
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=20.0) as client:
        matches = await fetch_current_matches(client)
        live_matches = select_live_matches(matches)
        next_match = select_next_match(matches, now)

        for match in matches:
            if not match_contains_keyword(match):
                continue

            match_id = str(match.get("id") or match.get("unique_id") or match.get("name"))
            if not match_id:
                continue

            digest = build_match_digest(match)
            previous_digest = state["matches"].get(match_id)
            state["matches"][match_id] = digest

            if is_live_match(match):
                if previous_digest == digest:
                    continue
                team_a, team_b = extract_teams(match)
                status = str(match.get("status", "Live"))
                title = detect_live_event(previous_digest, match)
                await post_to_targets(
                    client,
                    auto_live_update(title, team_a, team_b, status, format_score_lines(match)),
                )
                continue

            if previous_digest is None and bool(match.get("matchEnded")):
                continue

            if previous_digest != digest and is_recent_result(match, now):
                team_a, team_b = extract_teams(match)
                await post_to_targets(
                    client,
                    auto_live_update("Match Result", team_a, team_b, str(match.get("status", "")), format_score_lines(match)),
                )

        if next_match:
            next_match_id = str(next_match.get("id") or next_match.get("name"))
            message, bucket_key = build_next_match_message(next_match, now)
            last_bucket = state["next_match"].get(next_match_id)
            if last_bucket != bucket_key:
                await post_to_targets(client, message)
                state["next_match"] = {next_match_id: bucket_key}

            poll_key = f"{next_match_id}:{today}"
            if poll_key not in state["polls"]:
                team_a, team_b = extract_teams(next_match)
                await send_daily_poll(client, team_a, team_b)
                state["polls"][poll_key] = True

        news_items = await fetch_news_items(client, live_matches, next_match)
        for item in news_items:
            if item["id"] in state["news"]:
                continue
            state["news"][item["id"]] = today
            await post_to_targets(
                client,
                build_news_message(
                    item["title"],
                    item.get("description", ""),
                    item["link"],
                    item.get("source", ""),
                ),
            )

    save_state(state)


if __name__ == "__main__":
    asyncio.run(run_once())
