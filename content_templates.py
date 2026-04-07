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
        f"Today Match: {team_a} vs {team_b}\n"
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
    return f"Toss Update\n{text}"


def score_update(team: str, score: str, overs: str) -> str:
    return f"Live Score\n{team}: {score} ({overs} ov)"


def wicket_alert(player: str, team_score: str, overs: str) -> str:
    return f"WICKET!\n{player}\nScore: {team_score} ({overs} ov)"


def result_summary(result: str, player_of_match: str) -> str:
    return (
        f"Match Result\n{result}\n"
        f"Player of the Match: {player_of_match}"
    )


def giveaway_post(amount: str) -> str:
    return (
        f"Giveaway Alert: Rs {amount}\n"
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
    scores = "\n".join(score_lines) if score_lines else "Score update abhi provider se sync ho raha hai."
    return (
        f"{title}\n"
        f"{team_a} vs {team_b}\n"
        f"{scores}\n"
        f"Status: {status}"
    )
