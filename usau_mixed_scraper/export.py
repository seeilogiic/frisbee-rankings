"""
Deduplicate game records and write output CSV files.

Each game appears once on the "home" team's schedule page and once on the
opponent's page.  We canonicalise each game into a single row by sorting the
two TeamIds and using that as the dedup key together with event + date + score.

Output files
------------
games.csv  — one row per unique completed game
    date, event, team_a, team_a_id, team_b, team_b_id,
    score_a, score_b, winner

teams.csv  — one row per scraped team
    team_id, team_name, school_name, city_state, region, section,
    competition_level, gender_division, team_page_url, wins, losses, games_scraped
"""

import csv
import json
import logging
import os
from collections import defaultdict
from pathlib import Path

from .schedule import GameRecord, TeamMeta

logger = logging.getLogger(__name__)

GAMES_COLUMNS = [
    "date", "event",
    "team_a", "team_a_id",
    "team_b", "team_b_id",
    "score_a", "score_b",
    "winner",
]

TEAMS_COLUMNS = [
    "team_id", "team_name", "school_name",
    "city_state", "region", "section",
    "competition_level", "gender_division",
    "team_page_url", "wins", "losses", "games_scraped",
]


def deduplicate_games(all_games: list[GameRecord]) -> list[dict]:
    """
    Merge game records so each real game appears exactly once.

    Dedup key: frozenset({team_id, opponent_team_id}) + event + date + score.
    When neither or only one side has an opponent_team_id we fall back to
    frozenset({team_id, opponent_name}) to avoid losing cross-division data.

    Returns a list of dicts with GAMES_COLUMNS keys.
    """
    seen: set[tuple] = set()
    rows: list[dict] = []

    for g in all_games:
        key = _game_key(g)
        if key in seen:
            continue
        seen.add(key)

        # Canonical orientation: sort team ids so team_a < team_b lexically
        opp_id = g.opponent_team_id or f"__name__{g.opponent_name}"
        if g.team_id <= opp_id:
            team_a, a_id = g.team_name, g.team_id
            team_b, b_id = g.opponent_name, g.opponent_team_id or ""
            score_a, score_b = g.score_us, g.score_them
        else:
            team_a, a_id = g.opponent_name, g.opponent_team_id or ""
            team_b, b_id = g.team_name, g.team_id
            score_a, score_b = g.score_them, g.score_us

        if score_a is not None and score_b is not None:
            winner = team_a if score_a > score_b else (team_b if score_b > score_a else "tie")
        else:
            winner = ""

        rows.append({
            "date": g.date,
            "event": g.event,
            "team_a": team_a,
            "team_a_id": a_id,
            "team_b": team_b,
            "team_b_id": b_id,
            "score_a": score_a if score_a is not None else "",
            "score_b": score_b if score_b is not None else "",
            "winner": winner,
        })

    # Sort by date then event then team_a
    rows.sort(key=lambda r: (r["date"], r["event"], r["team_a"]))
    return rows


def write_games_csv(rows: list[dict], path: str) -> None:
    """Write deduplicated game rows to *path*."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=GAMES_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d game rows to %s", len(rows), path)


def write_teams_csv(metas: list[TeamMeta], path: str) -> None:
    """Write team metadata rows to *path*."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    rows = [
        {
            "team_id": m.team_id,
            "team_name": m.team_name,
            "school_name": m.school_name,
            "city_state": m.location,
            "region": m.region,
            "section": m.section,
            "competition_level": m.competition_level,
            "gender_division": m.gender_division,
            "team_page_url": "https://play.usaultimate.org" + m.team_path,
            "wins": m.wins,
            "losses": m.losses,
            "games_scraped": m.games_scraped,
        }
        for m in sorted(metas, key=lambda m: m.team_name.lower())
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TEAMS_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d team rows to %s", len(rows), path)


def write_metadata(meta: dict, path) -> None:
    """Overwrite out/metadata.json with scrape/rankings metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    logger.info("Wrote metadata to %s", path)


def append_history_snapshot(rankings: list[dict], path: str | Path, snapshot_date: str) -> None:
    """
    Append (or overwrite same-day) a snapshot of every team's rating and rank
    to the history file at *path*.

    Each snapshot is::

        {"date": "2026-06-30", "teams": {"TEAM_ID": {"rating": 5275.5, "rank": 1}, ...}}

    Same-day calls overwrite the existing entry so repeated daily runs don't
    inflate the file.  Entries are kept sorted ascending by date.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing history
    history: list[dict] = []
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []

    # Remove any existing entry for today (overwrite semantics)
    history = [entry for entry in history if entry.get("date") != snapshot_date]

    # Build new snapshot
    snapshot = {
        "date": snapshot_date,
        "teams": {
            r["team_id"]: {"rating": round(r["rating"], 2), "rank": r["rank"]}
            for r in rankings
        },
    }
    history.append(snapshot)
    history.sort(key=lambda e: e["date"])

    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, separators=(",", ":"))
    logger.info("Wrote history snapshot for %s to %s  (%d teams)", snapshot_date, path, len(rankings))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _game_key(g: GameRecord) -> tuple:
    """
    A hashable key that is the same from both sides of a game.
    Uses frozenset so order of team_id / opponent_team_id doesn't matter.
    """
    team_side = g.team_id
    opp_side = g.opponent_team_id if g.opponent_team_id else f"__name__{g.opponent_name}"
    return (
        frozenset({team_side, opp_side}),
        g.event,
        g.date,
        # Include both possible score orderings to handle the case where one
        # side logs "15-12" and the other logs "12-15"
        frozenset({g.score_us, g.score_them}),
    )
