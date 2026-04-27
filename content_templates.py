from dataclasses import dataclass


@dataclass(frozen=True)
class BrandConfig:
    handle: str
    hashtags: str


def attach_cta(message: str, brand: BrandConfig) -> str:
    return (
        f"{message}\n\n"
        f"Join Fastest IPL Live Score\n"
        f"{brand.handle}\n\n"
        f"{brand.hashtags}"
    )


def morning_preview(team_a: str, team_b: str, venue: str, pitch: str, captain: str, vice_captain: str) -> str:
    return (
        f"Today Match Preview\n"
        f"{team_a} vs {team_b}\n"
        f"Venue: {venue}\n"
        f"Pitch: {pitch}\n"
        f"Fantasy Captain: {captain}\n"
        f"Vice Captain: {vice_captain}"
    )


def engagement_poll(team_a: str, team_b: str) -> str:
    return (
        f"Aaj ka match kaun jeetega?\n"
        f"Like for {team_a}\n"
        f"Heart for {team_b}"
    )


def toss_update(text: str) -> str:
    return f"TOSS UPDATE\n{text}"


def score_update(team: str, score: str, overs: str) -> str:
    return f"LIVE SCORE\n{team}: {score} ({overs} ov)"


def wicket_alert(player: str, team_score: str, overs: str) -> str:
    return (
        f"WICKET!\n"
        f"{player}\n"
        f"Score: {team_score} ({overs} ov)\n"
        f"Big moment! Stay tuned."
    )


def result_summary(result: str, player_of_match: str) -> str:
    return (
        f"MATCH RESULT\n"
        f"{result}\n"
        f"Player of the Match: {player_of_match}"
    )


def giveaway_post(amount: str) -> str:
    return (
        f"GIVEAWAY ALERT: Rs {amount}\n"
        f"1. Join the group\n"
        f"2. Invite 5 friends\n"
        f"3. Send screenshot to admin"
    )


def auto_live_update(
    title: str,
    team_a: str,
    team_b: str,
    status: str,
    score_lines: list[str],
) -> str:
    scores = "\n".join(score_lines) if score_lines else "Score update sync ho raha hai..."
    return (
        f"{title}\n"
        f"{team_a} vs {team_b}\n"
        f"{scores}\n"
        f"Status: {status}"
    )


def styled_live_update(
    title: str,
    team_a: str,
    team_b: str,
    status: str,
    score_lines: list[str],
) -> str:
    score_block = "\n".join(f"  {line}" for line in score_lines) if score_lines else "  Score sync ho raha hai..."
    return (
        f"{title}\n"
        f"{team_a} vs {team_b}\n"
        f"{score_block}\n"
        f"Status: {status}\n"
        f"Next update soon. Stay tuned!"
    )


def styled_news_message(summary: str, source: str, link: str) -> str:
    return (
        f"IPL NEWS UPDATE\n"
        f"{summary}\n"
        f"Source: {source}\n"
        f"Read more: {link}"
    )


def styled_countdown_message(
    team_a: str,
    team_b: str,
    venue: str,
    countdown_text: str,
    status: str,
    match_date_str: str = "",
) -> str:
    date_line = f"Date: {match_date_str}\n" if match_date_str else ""
    return (
        f"MATCH COUNTDOWN\n"
        f"{team_a} vs {team_b}\n"
        f"{date_line}"
        f"Venue: {venue}\n"
        f"{countdown_text}\n"
        f"Update: {status}"
    )


def welcome_message(name: str, brand: BrandConfig) -> str:
    return (
        f"Welcome {name}!\n\n"
        f"IPL 2026 ke fastest live update group me aapka swagat hai!\n\n"
        f"Yahan milega:\n"
        f"  Ball-by-ball live score\n"
        f"  Instant wicket alerts\n"
        f"  Toss aur match updates\n"
        f"  Fantasy tips aur captain picks\n"
        f"  Match polls aur debates\n"
        f"  Points table updates\n\n"
        f"Apne dosto ko bhi invite karo aur IPL ka maza double karo!\n\n"
        f"Join: {brand.handle}\n"
        f"{brand.hashtags}"
    )


DEBATE_TEMPLATES = [
    ("Aaj ka sabse bada match-winner kaun hoga?", ["{team_a} ka captain", "{team_b} ka captain", "Koi bowler"]),
    ("Is IPL 2026 ka best team kaun hai abhi tak?", ["{team_a}", "{team_b}", "Dono equally strong"]),
    ("Aaj toss jeetna kitna important hai?", ["Bahut important", "Koi fark nahi", "Depends on pitch"]),
    ("Aaj ka match kitne overs tak decide ho jayega?", ["Powerplay me", "Middle overs me", "Last 5 overs me"]),
    ("{team_a} vs {team_b} - rivalry me history kiska favor karta hai?", ["{team_a}", "{team_b}", "50-50 hai"]),
]

_debate_index: list[int] = [0]


def debate_post(team_a: str, team_b: str) -> tuple[str, list[str]]:
    idx = _debate_index[0] % len(DEBATE_TEMPLATES)
    _debate_index[0] += 1
    question_template, options_template = DEBATE_TEMPLATES[idx]
    question = question_template.format(team_a=team_a, team_b=team_b)
    options = [opt.format(team_a=team_a, team_b=team_b) for opt in options_template]
    return question, options


def points_table_impact(winner: str, loser: str, match_name: str) -> str:
    return (
        f"POINTS TABLE UPDATE\n"
        f"{match_name}\n\n"
        f"{winner} ne match jeeta!\n"
        f"+2 points milenge {winner} ko\n"
        f"{loser} ko agle match me wapsi karni hogi\n\n"
        f"Playoff race tight ho rahi hai! Apna prediction comment karo."
    )
