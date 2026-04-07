import asyncio
import contextlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx
from dotenv import load_dotenv

from content_templates import BrandConfig, attach_cta, auto_live_update


BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
STATE_FILE = STATE_DIR / "autoposter_state.json"

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
NEWS_RSS_URL = os.getenv(
    "NEWS_RSS_URL",
    "https://news.google.com/rss/search?q=IPL&hl=en-IN&gl=IN&ceid=IN:en",
)
MAX_NEWS_POSTS_PER_RUN = int(os.getenv("MAX_NEWS_POSTS_PER_RUN", "1"))
TELEGRAM_POST_DELAY_SECONDS = float(os.getenv("TELEGRAM_POST_DELAY_SECONDS", "1.5"))

brand = BrandConfig(handle=BRAND_HANDLE, hashtags=DEFAULT_HASHTAGS)


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def load_state() -> dict[str, Any]:
    ensure_state_dir()
    if not STATE_FILE.exists():
        return {"matches": {}, "news": {}, "polls": {}}

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
    return f"{status}::{scores}::{toss}"


async def post_message(client: httpx.AsyncClient, chat_id: str, message: str) -> None:
    if not chat_id:
        return

    payload = {"chat_id": chat_id, "text": attach_cta(message, brand)}

    for attempt in range(3):
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


async def fetch_news_items(client: httpx.AsyncClient) -> list[dict[str, str]]:
    response = await client.get(NEWS_RSS_URL)
    response.raise_for_status()
    root = ElementTree.fromstring(response.text)

    items: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="").strip()
        link = item.findtext("link", default="").strip()
        guid = item.findtext("guid", default=link).strip()
        if not title or not link:
            continue
        items.append({"id": guid, "title": title, "link": link})
    return items[:MAX_NEWS_POSTS_PER_RUN]


def build_news_message(title: str, link: str) -> str:
    return f"IPL News Update\n{title}\n{link}"


async def run_once() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not configured")

    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=20.0) as client:
        matches = await fetch_current_matches(client)
        for match in matches:
            if not match_contains_keyword(match):
                continue

            match_id = str(match.get("id") or match.get("unique_id") or match.get("name"))
            if not match_id:
                continue

            digest = build_match_digest(match)
            if state["matches"].get(match_id) != digest:
                state["matches"][match_id] = digest
                team_a, team_b = extract_teams(match)
                status = str(match.get("status", "Live"))
                title = "Live IPL Update"
                if "won" in status.lower():
                    title = "Match Result"
                elif "toss" in status.lower():
                    title = "Toss Update"

                await post_to_targets(
                    client,
                    auto_live_update(title, team_a, team_b, status, format_score_lines(match)),
                )

            poll_key = f"{match_id}:{today}"
            if poll_key not in state["polls"]:
                team_a, team_b = extract_teams(match)
                await send_daily_poll(client, team_a, team_b)
                state["polls"][poll_key] = True

        news_items = await fetch_news_items(client)
        for item in news_items:
            if item["id"] in state["news"]:
                continue
            state["news"][item["id"]] = today
            await post_to_targets(client, build_news_message(item["title"], item["link"]))

    save_state(state)


if __name__ == "__main__":
    asyncio.run(run_once())
