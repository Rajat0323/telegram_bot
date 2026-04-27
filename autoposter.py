import asyncio
import contextlib
import html
import json
import os
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree

IST = timezone(timedelta(hours=5, minutes=30))

import httpx
from dotenv import load_dotenv

from content_templates import (
    BrandConfig,
    attach_cta,
    auto_live_update,
    points_table_impact,
    styled_countdown_message,
    styled_live_update,
    styled_news_message,
)


BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
STATE_FILE = STATE_DIR / "autoposter_state.json"
API_ROOT = "https://api.cricapi.com/v1"

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
API_DAILY_LIMIT = env_int("API_DAILY_LIMIT", 95)
HTTP_TIMEOUT_SECONDS = env_float("HTTP_TIMEOUT_SECONDS", 30.0)
HTTP_RETRY_COUNT = env_int("HTTP_RETRY_COUNT", 3)
HTTP_RETRY_DELAY_SECONDS = env_float("HTTP_RETRY_DELAY_SECONDS", 2.0)
ENABLE_NEWS_IMAGES = env_str("ENABLE_NEWS_IMAGES", "true").lower() == "true"

ENDPOINT_CADENCE_MINUTES = {
    "cricScore": env_int("CRICSCORE_CADENCE_MINUTES", 30),
    "matches": env_int("MATCHES_CADENCE_MINUTES", 180),
    "currentMatches": env_int("CURRENTMATCHES_CADENCE_MINUTES", 120),
    "series": env_int("SERIES_CADENCE_MINUTES", 720),
    "series_info": env_int("SERIESINFO_CADENCE_MINUTES", 360),
    "series_squad": env_int("SQUAD_CADENCE_MINUTES", 720),
    "match_scorecard": env_int("SCORECARD_CADENCE_MINUTES", 1440),
    "match_points": env_int("POINTS_CADENCE_MINUTES", 1440),
    "players_info": env_int("PLAYERINFO_CADENCE_MINUTES", 1440),
}

brand = BrandConfig(handle=BRAND_HANDLE, hashtags=DEFAULT_HASHTAGS)


def ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def empty_state() -> dict[str, Any]:
    return {
        "matches": {},
        "news": {},
        "polls": {},
        "next_match": {},
        "api_usage": {"date": "", "count": 0},
        "endpoint_meta": {},
        "payload_cache": {},
        "details_posted": {"scorecard": {}, "points": {}, "player": {}, "squad": {}},
        "series": {"id": "", "name": ""},
    }


def load_state() -> dict[str, Any]:
    ensure_state_dir()
    if not STATE_FILE.exists():
        return empty_state()

    with STATE_FILE.open("r", encoding="utf-8") as file:
        data = json.load(file)

    state = empty_state()
    state.update(data)
    state["api_usage"] = {**empty_state()["api_usage"], **data.get("api_usage", {})}
    state["endpoint_meta"] = data.get("endpoint_meta", {})
    state["payload_cache"] = data.get("payload_cache", {})
    state["details_posted"] = {**empty_state()["details_posted"], **data.get("details_posted", {})}
    state["series"] = {**empty_state()["series"], **data.get("series", {})}
    return state


def save_state(state: dict[str, Any]) -> None:
    ensure_state_dir()
    with STATE_FILE.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=True, indent=2)


def reset_daily_usage(state: dict[str, Any], today: str) -> None:
    if state["api_usage"].get("date") != today:
        state["api_usage"] = {"date": today, "count": 0}


def api_budget_remaining(state: dict[str, Any]) -> int:
    return max(0, API_DAILY_LIMIT - int(state["api_usage"].get("count", 0)))


def update_endpoint_meta(state: dict[str, Any], name: str, now: datetime) -> None:
    state["endpoint_meta"].setdefault(name, {})["last_called"] = now.isoformat()


def endpoint_due(state: dict[str, Any], name: str, now: datetime) -> bool:
    meta = state["endpoint_meta"].get(name, {})
    last_called = meta.get("last_called")
    if not last_called:
        return True
    with contextlib.suppress(ValueError):
        last_time = datetime.fromisoformat(last_called)
        minutes = (now - last_time).total_seconds() / 60
        return minutes >= ENDPOINT_CADENCE_MINUTES.get(name, 60)
    return True


async def api_get(
    client: httpx.AsyncClient,
    state: dict[str, Any],
    name: str,
    params: dict[str, Any],
    now: datetime,
    force: bool = False,
) -> Any | None:
    if not CRICKET_API_KEY:
        return state["payload_cache"].get(name)

    if not force and not endpoint_due(state, name, now):
        return state["payload_cache"].get(name)

    if api_budget_remaining(state) <= 0:
        return state["payload_cache"].get(name)

    payload = None
    for attempt in range(HTTP_RETRY_COUNT):
        try:
            response = await client.get(f"{API_ROOT}/{name}", params={"apikey": CRICKET_API_KEY, **params})
            response.raise_for_status()
            payload = response.json()
            break
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ConnectError):
            if attempt == HTTP_RETRY_COUNT - 1:
                return state["payload_cache"].get(name)
            await asyncio.sleep(HTTP_RETRY_DELAY_SECONDS * (attempt + 1))

    if payload is None:
        return state["payload_cache"].get(name)

    state["api_usage"]["count"] = int(state["api_usage"].get("count", 0)) + 1
    update_endpoint_meta(state, name, now)

    data = payload.get("data") if isinstance(payload, dict) else payload
    status = payload.get("status") if isinstance(payload, dict) else None
    if status == "failure":
        return state["payload_cache"].get(name)

    state["payload_cache"][name] = data
    return data


def strip_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_image_url(raw_html: str) -> str | None:
    patterns = [
        r'<img[^>]+src="([^"]+)"',
        r"<img[^>]+src='([^']+)'",
        r"https://[^\\s\"']+\\.(?:jpg|jpeg|png|webp)",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_html, flags=re.IGNORECASE)
        if match:
            return html.unescape(match.group(1))
    return None


def summarize_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return " ".join(words)
    return " ".join(words[:limit]).rstrip(" ,.") + "..."


def match_contains_keyword(match: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(match.get(field, ""))
        for field in ("name", "series", "series_id", "matchType", "status", "venue", "t1", "t2")
    ).lower()
    return any(keyword in haystack for keyword in TOURNAMENT_KEYWORDS)


def extract_teams(match: dict[str, Any]) -> tuple[str, str]:
    if match.get("t1") and match.get("t2"):
        return str(match["t1"]).split(" [", 1)[0], str(match["t2"]).split(" [", 1)[0]

    team_info = match.get("teamInfo") or []
    if len(team_info) >= 2:
        return str(team_info[0].get("name", "Team A")), str(team_info[1].get("name", "Team B"))

    teams = match.get("teams") or []
    if len(teams) >= 2:
        return str(teams[0]), str(teams[1])

    return "Team A", "Team B"


def parse_match_time(match: dict[str, Any]) -> datetime | None:
    raw = match.get("dateTimeGMT")
    if not raw:
        return None
    with contextlib.suppress(ValueError):
        return datetime.fromisoformat(str(raw)).replace(tzinfo=UTC)
    return None


def match_is_today_or_tomorrow(match_time: datetime, now: datetime) -> bool:
    now_ist = now.astimezone(IST)
    match_ist = match_time.astimezone(IST)
    today = now_ist.date()
    match_date = match_ist.date()
    return match_date == today or match_date == (today + timedelta(days=1))


def parse_runs_wickets(score_text: str) -> tuple[int | None, int | None]:
    match = re.search(r"(\d+)-(\d+)", score_text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def score_lines_from_cricscore(item: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    t1, t2 = extract_teams(item)
    if item.get("t1s"):
        lines.append(f"{t1}: {item['t1s']}")
    if item.get("t2s"):
        lines.append(f"{t2}: {item['t2s']}")
    return lines


def score_lines_from_current(match: dict[str, Any]) -> list[str]:
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


def build_digest(status: str, score_lines: list[str]) -> str:
    return f"{status}::{' | '.join(score_lines)}"


def is_recent_result(match: dict[str, Any], now: datetime) -> bool:
    if not bool(match.get("matchEnded")):
        return False
    match_time = parse_match_time(match)
    if match_time is None:
        return False
    return (now - match_time).total_seconds() <= 24 * 3600


def is_live_status(item: dict[str, Any]) -> bool:
    ms = str(item.get("ms", "")).lower()
    status = str(item.get("status", "")).lower()
    if ms in {"live", "result"}:
        return True
    return any(keyword in status for keyword in ("delay", "delayed", "rain", "outfield", "inning", "need", "won by", "target"))


def select_live_feed_items(cric_score: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in cric_score if match_contains_keyword(item) and is_live_status(item)]


def select_next_match(matches: list[dict[str, Any]], series_info: dict[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    candidates = [m for m in matches if match_contains_keyword(m) and not bool(m.get("matchStarted"))]
    if series_info:
        for match in series_info.get("matchList", []):
            if match_contains_keyword(match) and not bool(match.get("matchStarted")):
                candidates.append(match)
    if not candidates:
        return None
    candidates = [c for c in candidates if (parse_match_time(c) or now) >= now]
    if not candidates:
        return None
    sorted_candidates = sorted(candidates, key=lambda item: parse_match_time(item) or datetime.max.replace(tzinfo=UTC))
    for candidate in sorted_candidates:
        mt = parse_match_time(candidate)
        if mt and match_is_today_or_tomorrow(mt, now):
            return candidate
    return None


def describe_countdown(match_time: datetime, now: datetime) -> tuple[str, int]:
    remaining_minutes = max(0, int((match_time - now).total_seconds() // 60))
    bucket = remaining_minutes // max(1, COUNTDOWN_BUCKET_MINUTES)
    if remaining_minutes >= 1440:
        return f"Starts in {remaining_minutes // 1440} day(s)", bucket
    if remaining_minutes >= 60:
        return f"Starts in {remaining_minutes // 60} hour(s) {remaining_minutes % 60} min", bucket
    return f"Starts in {remaining_minutes} min", bucket


def build_next_match_message(match: dict[str, Any], now: datetime) -> tuple[str, str]:
    team_a, team_b = extract_teams(match)
    match_time = parse_match_time(match)
    venue = str(match.get("venue", "Venue update soon"))
    status = str(match.get("status", "Upcoming"))
    if match_time is None:
        countdown_text = "Schedule update soon"
        bucket_key = f"{match.get('id', 'unknown')}:unknown"
        date_str = ""
    else:
        countdown_text, bucket = describe_countdown(match_time, now)
        bucket_key = f"{match.get('id', 'unknown')}:{bucket}"
        ist_time = match_time.astimezone(IST)
        date_str = ist_time.strftime("%d %b %Y, %I:%M %p IST")

    message = styled_countdown_message(
        team_a=team_a,
        team_b=team_b,
        venue=venue,
        countdown_text=countdown_text,
        status=status,
        match_date_str=date_str,
    )
    return message, bucket_key


def detect_live_event(previous_digest: str | None, item: dict[str, Any], score_lines: list[str]) -> str:
    status = str(item.get("status", "")).lower()
    if any(keyword in status for keyword in ("delay", "delayed", "rain", "outfield")):
        return "Match Delay"
    if not previous_digest:
        return "Live IPL Update"

    prev_status, _, prev_scores = previous_digest.partition("::")
    if prev_status != str(item.get("status", "")) and any(keyword in status for keyword in ("toss", "delay", "won")):
        return "Status Update"

    previous_runs = previous_wickets = None
    if prev_scores:
        last_part = prev_scores.split(" | ")[-1]
        previous_runs, previous_wickets = parse_runs_wickets(last_part)

    if not score_lines:
        return "Live IPL Update"

    current_runs, current_wickets = parse_runs_wickets(score_lines[-1])
    if previous_wickets is not None and current_wickets is not None and current_wickets > previous_wickets:
        return "Wicket Alert"
    if previous_runs is not None and current_runs is not None:
        for milestone in (50, 100, 150, 200):
            if previous_runs < milestone <= current_runs:
                return f"{milestone} Up"
        if current_runs - previous_runs >= 6:
            return "Big Over Update"
    return "Live IPL Update"


def build_news_query(live_items: list[dict[str, Any]], next_match: dict[str, Any] | None) -> str:
    keywords = ["IPL"]
    for item in live_items[:2]:
        team_a, team_b = extract_teams(item)
        keywords.extend([team_a, team_b])
    if next_match:
        team_a, team_b = extract_teams(next_match)
        keywords.extend([team_a, team_b, "preview"])
    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        normalized = keyword.lower().strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(keyword)
    return " OR ".join(deduped[:6])


def build_news_url(query: str) -> str:
    if "{query}" in NEWS_RSS_URL:
        return NEWS_RSS_URL.format(query=quote_plus(query))
    return NEWS_RSS_URL


def title_matches_context(title: str, live_items: list[dict[str, Any]], next_match: dict[str, Any] | None) -> bool:
    lowered = title.lower()
    if "ipl" not in lowered and "indian premier league" not in lowered:
        return False
    for item in live_items:
        team_a, team_b = extract_teams(item)
        if team_a.lower() in lowered or team_b.lower() in lowered:
            return True
    if next_match:
        team_a, team_b = extract_teams(next_match)
        return team_a.lower() in lowered or team_b.lower() in lowered or "preview" in lowered
    return True


def build_news_message(title: str, description: str, link: str, source: str) -> str:
    body = description or title
    summary = summarize_words(f"{title}. {body}", NEWS_SUMMARY_WORD_LIMIT)
    source_text = source or "News feed"
    return styled_news_message(summary, source_text, link)


def build_scorecard_message(match_name: str, scorecard: dict[str, Any]) -> str | None:
    entries = scorecard.get("scorecard") or []
    if not entries:
        return None
    first_innings = entries[0]
    batting = first_innings.get("batting") or []
    bowling = first_innings.get("bowling") or []
    top_bat = max(batting, key=lambda item: item.get("r", -1), default=None)
    top_bowl = max(bowling, key=lambda item: item.get("w", -1), default=None)
    parts = [f"Match Breakdown\n{match_name}"]
    if top_bat:
        parts.append(f"Top Bat: {top_bat.get('batsman', {}).get('name', 'Unknown')} {top_bat.get('r', 0)}")
    if top_bowl:
        parts.append(f"Top Bowl: {top_bowl.get('bowler', {}).get('name', 'Unknown')} {top_bowl.get('w', 0)} wickets")
    return "\n".join(parts)


def top_points_player(points_data: dict[str, Any]) -> tuple[str | None, str | None, int | None]:
    best_id = best_name = None
    best_points: int | None = None
    for innings in points_data.get("innings", []):
        for bucket in ("batting", "bowling", "catching"):
            for item in innings.get(bucket, []):
                points = item.get("points")
                if not isinstance(points, int):
                    continue
                if best_points is None or points > best_points:
                    best_points = points
                    best_id = item.get("id")
                    best_name = item.get("name")
    return best_id, best_name, best_points


def build_player_spotlight(match_name: str, player_name: str, points: int | None, player: dict[str, Any] | None) -> str:
    role = player.get("role", "Player") if player else "Player"
    country = player.get("country", "") if player else ""
    suffix = f" | {country}" if country else ""
    pts = f"{points} pts" if points is not None else "Top points"
    return f"Fantasy Spotlight\n{match_name}\n{player_name} - {pts}\nRole: {role}{suffix}"


def build_squad_watch(next_match: dict[str, Any], squads: list[dict[str, Any]]) -> str | None:
    team_a, team_b = extract_teams(next_match)
    wanted = {team_a.lower(), team_b.lower()}
    selected = [team for team in squads if str(team.get("teamName", "")).lower() in wanted]
    if len(selected) < 2:
        return None
    lines = [f"Squad Watch\n{team_a} vs {team_b}"]
    for team in selected[:2]:
        players = team.get("players") or []
        highlights = ", ".join(player.get("name", "") for player in players[:3] if player.get("name"))
        if highlights:
            lines.append(f"{team.get('shortname', team.get('teamName', 'Team'))}: {highlights}")
    return "\n".join(lines)


async def post_message(client: httpx.AsyncClient, chat_id: str, message: str) -> None:
    if not chat_id:
        return
    payload = {"chat_id": chat_id, "text": attach_cta(message, brand)}
    response: httpx.Response | None = None
    for attempt in range(max(3, HTTP_RETRY_COUNT)):
        try:
            response = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ConnectError):
            if attempt == max(3, HTTP_RETRY_COUNT) - 1:
                raise
            await asyncio.sleep(HTTP_RETRY_DELAY_SECONDS * (attempt + 1))
            continue
        if response.status_code != 429:
            response.raise_for_status()
            await asyncio.sleep(TELEGRAM_POST_DELAY_SECONDS)
            return
        retry_after = 5
        with contextlib.suppress(Exception):
            retry_after = int(response.json().get("parameters", {}).get("retry_after", retry_after))
        await asyncio.sleep(retry_after + 1)
    if response is not None:
        response.raise_for_status()


async def post_photo_message(client: httpx.AsyncClient, chat_id: str, image_url: str, caption: str) -> None:
    if not chat_id or not image_url:
        return
    payload = {"chat_id": chat_id, "photo": image_url, "caption": attach_cta(caption, brand)}
    response: httpx.Response | None = None
    for attempt in range(max(3, HTTP_RETRY_COUNT)):
        try:
            response = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", json=payload)
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ConnectError):
            if attempt == max(3, HTTP_RETRY_COUNT) - 1:
                raise
            await asyncio.sleep(HTTP_RETRY_DELAY_SECONDS * (attempt + 1))
            continue
        if response.status_code != 429:
            if response.status_code == 400:
                await post_message(client, chat_id, caption)
                return
            response.raise_for_status()
            await asyncio.sleep(TELEGRAM_POST_DELAY_SECONDS)
            return
        retry_after = 5
        with contextlib.suppress(Exception):
            retry_after = int(response.json().get("parameters", {}).get("retry_after", retry_after))
        await asyncio.sleep(retry_after + 1)
    if response is not None:
        response.raise_for_status()


async def post_to_targets(client: httpx.AsyncClient, message: str) -> None:
    await post_message(client, CHANNEL_CHAT_ID, message)
    await post_message(client, GROUP_CHAT_ID, message)


async def post_photo_to_targets(client: httpx.AsyncClient, image_url: str, caption: str) -> None:
    await post_photo_message(client, CHANNEL_CHAT_ID, image_url, caption)
    await post_photo_message(client, GROUP_CHAT_ID, image_url, caption)


async def send_daily_poll(client: httpx.AsyncClient, team_a: str, team_b: str) -> None:
    if not GROUP_CHAT_ID:
        return
    payload = {
        "chat_id": GROUP_CHAT_ID,
        "question": "Aaj ka IPL match kaun jeetega?",
        "options": [team_a, team_b],
        "is_anonymous": False,
    }
    response: httpx.Response | None = None
    for attempt in range(HTTP_RETRY_COUNT):
        try:
            response = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPoll", json=payload)
            response.raise_for_status()
            break
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ConnectError):
            if attempt == HTTP_RETRY_COUNT - 1:
                raise
            await asyncio.sleep(HTTP_RETRY_DELAY_SECONDS * (attempt + 1))
    if response is None:
        return
    await asyncio.sleep(TELEGRAM_POST_DELAY_SECONDS)


async def fetch_news_items(
    client: httpx.AsyncClient,
    live_items: list[dict[str, Any]],
    next_match: dict[str, Any] | None,
) -> list[dict[str, str]]:
    response = None
    for attempt in range(HTTP_RETRY_COUNT):
        try:
            response = await client.get(build_news_url(build_news_query(live_items, next_match)))
            response.raise_for_status()
            break
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ConnectError):
            if attempt == HTTP_RETRY_COUNT - 1:
                return []
            await asyncio.sleep(HTTP_RETRY_DELAY_SECONDS * (attempt + 1))
    if response is None:
        return []
    root = ElementTree.fromstring(response.text)
    items: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = item.findtext("title", default="").strip()
        link = item.findtext("link", default="").strip()
        guid = item.findtext("guid", default=link).strip()
        raw_description = item.findtext("description", default="").strip()
        description = strip_html(raw_description)
        source = item.findtext("source", default="").strip()
        image_url = extract_image_url(raw_description)
        if not title or not link:
            continue
        if not title_matches_context(title, live_items, next_match):
            continue
        items.append(
            {
                "id": guid,
                "title": title,
                "link": link,
                "description": description,
                "source": source,
                "image_url": image_url or "",
            }
        )
        if len(items) >= MAX_NEWS_POSTS_PER_RUN:
            break
    return items


def find_ipl_series_id(series_list: list[dict[str, Any]], matches_list: list[dict[str, Any]]) -> tuple[str, str]:
    for item in series_list:
        if "indian premier league" in str(item.get("name", "")).lower() and "2026" in str(item.get("name", "")):
            return str(item.get("id", "")), str(item.get("name", ""))
    for match in matches_list:
        if match_contains_keyword(match) and match.get("series_id"):
            return str(match["series_id"]), "Indian Premier League"
    return "", ""


def build_live_view(item: dict[str, Any], current_map: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    current = current_map.get(str(item.get("id", "")))
    if current:
        return str(current.get("status", item.get("status", "Live"))), score_lines_from_current(current)
    return str(item.get("status", "Live")), score_lines_from_cricscore(item)


async def run_once() -> None:
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not configured")

    state = load_state()
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    reset_daily_usage(state, today)

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        cric_score = await api_get(client, state, "cricScore", {}, now) or []
        matches = await api_get(client, state, "matches", {"offset": 0}, now) or []
        current_matches = await api_get(client, state, "currentMatches", {"offset": 0}, now) or []
        series_list = await api_get(client, state, "series", {"offset": 0, "search": "Indian Premier League 2026"}, now) or []

        series_id, series_name = find_ipl_series_id(series_list, matches)
        if series_id:
            state["series"] = {"id": series_id, "name": series_name}
        else:
            series_id = state["series"].get("id", "")

        series_info = None
        if series_id:
            series_info = await api_get(client, state, "series_info", {"id": series_id}, now)

        current_map = {str(item.get("id", "")): item for item in current_matches if str(item.get("id", ""))}
        live_items = select_live_feed_items(cric_score)

        for item in live_items:
            match_id = str(item.get("id", ""))
            team_a, team_b = extract_teams(item)
            status, score_lines = build_live_view(item, current_map)
            digest = build_digest(status, score_lines)
            previous_digest = state["matches"].get(match_id)
            if previous_digest == digest:
                continue
            state["matches"][match_id] = digest
            title = detect_live_event(previous_digest, {"status": status}, score_lines)
            await post_to_targets(client, styled_live_update(title, team_a, team_b, status, score_lines))

        for match in current_matches:
            if not match_contains_keyword(match):
                continue
            if not is_recent_result(match, now):
                continue
            match_id = str(match.get("id", ""))
            digest = build_digest(str(match.get("status", "")), score_lines_from_current(match))
            previous_digest = state["matches"].get(match_id)
            state["matches"][match_id] = digest
            if previous_digest == digest or state["details_posted"]["scorecard"].get(match_id):
                continue

            team_a_res, team_b_res = extract_teams(match)
            result_status = str(match.get("status", ""))
            await post_to_targets(
                client,
                styled_live_update(
                    "Match Result",
                    team_a_res,
                    team_b_res,
                    result_status,
                    score_lines_from_current(match),
                ),
            )

            winner_res = team_a_res if team_a_res.lower() in result_status.lower() else team_b_res
            loser_res = team_b_res if winner_res == team_a_res else team_a_res
            await post_to_targets(
                client,
                points_table_impact(winner_res, loser_res, str(match.get("name", f"{team_a_res} vs {team_b_res}"))),
            )

            scorecard = await api_get(client, state, "match_scorecard", {"id": match_id}, now, force=True)
            if isinstance(scorecard, dict):
                message = build_scorecard_message(str(match.get("name", "IPL Match")), scorecard)
                if message:
                    await post_to_targets(client, message)
                    state["details_posted"]["scorecard"][match_id] = today

            points = await api_get(client, state, "match_points", {"id": match_id, "ruleset": 0}, now, force=True)
            if isinstance(points, dict):
                player_id, player_name, top_points = top_points_player(points)
                if player_id and player_name and not state["details_posted"]["player"].get(match_id):
                    player = await api_get(client, state, "players_info", {"id": player_id}, now, force=True)
                    player_data = player if isinstance(player, dict) else None
                    await post_to_targets(
                        client,
                        build_player_spotlight(str(match.get("name", "IPL Match")), player_name, top_points, player_data),
                    )
                    state["details_posted"]["player"][match_id] = today
                state["details_posted"]["points"][match_id] = today

        next_match = select_next_match(matches, series_info if isinstance(series_info, dict) else None, now)
        if next_match:
            next_match_id = str(next_match.get("id") or next_match.get("name"))
            message, bucket_key = build_next_match_message(next_match, now)
            if state["next_match"].get(next_match_id) != bucket_key:
                await post_to_targets(client, message)
                state["next_match"] = {next_match_id: bucket_key}

            poll_key = f"{next_match_id}:{today}"
            if poll_key not in state["polls"]:
                team_a, team_b = extract_teams(next_match)
                await send_daily_poll(client, team_a, team_b)
                state["polls"][poll_key] = True

            if series_id and not state["details_posted"]["squad"].get(next_match_id):
                squad_data = await api_get(client, state, "series_squad", {"id": series_id}, now)
                if isinstance(squad_data, list):
                    squad_message = build_squad_watch(next_match, squad_data)
                    if squad_message:
                        await post_to_targets(client, squad_message)
                        state["details_posted"]["squad"][next_match_id] = today

        news_items = await fetch_news_items(client, live_items, next_match)
        for item in news_items:
            if item["id"] in state["news"]:
                continue
            state["news"][item["id"]] = today
            news_caption = build_news_message(item["title"], item["description"], item["link"], item["source"])
            if ENABLE_NEWS_IMAGES and item.get("image_url"):
                await post_photo_to_targets(client, item["image_url"], news_caption)
            else:
                await post_to_targets(client, news_caption)

    save_state(state)


if __name__ == "__main__":
    asyncio.run(run_once())
