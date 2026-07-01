"""
Scrape the season schedule / game log for a single team page.

The schedule table id is CT_Right_0_gvEventScheduleScores.  Its rows come in
two flavours:
  - Tournament-header row: exactly one <td> (spans the whole row), contains a
    link with the tournament name.
  - Game row: multiple <td> cells: [date, score, opponent, ...].
    The score cell's CSS class is "win", "loss", or something else (forfeit /
    draw / unplayed).

Source: port of erin2722/usau-scraper getTeamSchedule() with the addition of:
  - completed-only filter (score must match "N - N")
  - team metadata extraction (location, region, section, competition level)
"""

import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .client import USAUClient
from .teams import TeamRef, _extract_team_id

logger = logging.getLogger(__name__)

SCORE_RE = re.compile(r"^(\d+)\s*[-–]\s*(\d+)$")


@dataclass
class GameRecord:
    """One game from a team's season schedule."""
    team_id: str
    team_name: str
    date: str
    event: str
    result: str          # "win", "loss", or "unknown"
    score: str           # raw score text, e.g. "15 - 12"
    score_us: int | None
    score_them: int | None
    opponent_name: str
    opponent_team_id: str | None  # extracted from opponent href, or None


@dataclass
class TeamMeta:
    """Metadata from a team's profile page."""
    team_id: str
    team_name: str
    school_name: str
    competition_level: str
    gender_division: str
    location: str
    region: str
    section: str
    team_path: str
    wins: int = 0
    losses: int = 0
    games_scraped: int = 0


def scrape_team(client: USAUClient, team: TeamRef) -> tuple[TeamMeta, list[GameRecord]]:
    """
    Fetch a team's page and return (TeamMeta, [GameRecord, ...]).

    Only completed games (final numeric scores) are returned.
    """
    soup = client.get(team.team_path)
    meta = _parse_team_meta(soup, team)
    games = _parse_schedule(soup, meta)
    meta.games_scraped = len(games)
    meta.wins = sum(1 for g in games if g.result == "win")
    meta.losses = sum(1 for g in games if g.result == "loss")
    return meta, games


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_team_meta(soup: BeautifulSoup, team: TeamRef) -> TeamMeta:
    """Extract team metadata from the team profile page header."""
    school_name = ""
    team_name = team.name

    # The existing usau-scraper reads: soup.find(class_="profile_info").find("h4")
    # text like "School Name (Team Name)" or just "Team Name"
    profile = soup.find(class_="profile_info")
    if profile:
        h4 = profile.find("h4")
        if h4:
            raw = h4.get_text(strip=True)
            # "School (Team)" → split on last "("
            if "(" in raw and raw.endswith(")"):
                school_name = raw[: raw.rfind("(")].strip()
                team_name = raw[raw.rfind("(") + 1 : -1].strip()
            else:
                team_name = raw

    def _dd(id_: str) -> str:
        tag = soup.find(id=id_)
        if tag:
            dd = tag.find("dd")
            return dd.get_text(strip=True) if dd else tag.get_text(strip=True)
        return ""

    competition_level = _dd("CT_Main_0_ucTeamDetails_dlCompetitionLevel")
    gender_division = _dd("CT_Main_0_ucTeamDetails_dlGenderDivision")

    # City/State
    location_tag = soup.find(id="CT_Main_0_ucTeamDetails_dlCity")
    location = location_tag.get_text(strip=True) if location_tag else ""

    # Region / Section — these fields exist on Club team pages
    region = _dd("CT_Main_0_ucTeamDetails_dlRegion")
    section = _dd("CT_Main_0_ucTeamDetails_dlSection")

    return TeamMeta(
        team_id=team.team_id,
        team_name=team_name,
        school_name=school_name,
        competition_level=competition_level,
        gender_division=gender_division,
        location=location,
        region=region,
        section=section,
        team_path=team.team_path,
    )


def _parse_schedule(soup: BeautifulSoup, meta: TeamMeta) -> list[GameRecord]:
    """Parse the CT_Right_0_gvEventScheduleScores table into GameRecord objects."""
    table = soup.find(id="CT_Right_0_gvEventScheduleScores")
    if table is None:
        logger.debug("  No schedule table for %s", meta.team_name)
        return []

    games: list[GameRecord] = []
    current_event = ""

    for row in table.find_all("tr"):
        cells = row.find_all("td")

        if len(cells) == 1:
            # Tournament header row
            a = cells[0].find("a")
            current_event = a.get_text(strip=True) if a else cells[0].get_text(strip=True)
            continue

        if len(cells) < 3:
            continue

        # Game row
        # Cell 0: date
        span = cells[0].find("span")
        date = span.get_text(strip=True) if span else cells[0].get_text(strip=True)

        # Cell 1: score + win/loss
        score_cell = cells[1]
        score_text = ""
        score_a = score_cell.find("a")
        if score_a:
            score_text = score_a.get_text(strip=True)
        else:
            score_text = score_cell.get_text(strip=True)

        # Only keep completed games (numeric score)
        m = SCORE_RE.match(score_text)
        if not m:
            logger.debug("  Skipping non-final row: date=%s score=%r event=%s",
                         date, score_text, current_event)
            continue

        score_us = int(m.group(1))
        score_them = int(m.group(2))

        css_classes = score_cell.get("class") or []
        if isinstance(css_classes, str):
            css_classes = [css_classes]
        if "win" in css_classes:
            result = "win"
        elif "loss" in css_classes:
            result = "loss"
        else:
            # Infer from score if CSS class is missing
            if score_us > score_them:
                result = "win"
            elif score_us < score_them:
                result = "loss"
            else:
                result = "unknown"

        # Cell 2: opponent
        opp_cell = cells[2]
        opp_a = opp_cell.find("a")
        if opp_a:
            opponent_name = opp_a.get_text(strip=True)
            opp_href = opp_a.get("href", "")
            opponent_team_id = _extract_team_id(opp_href)
        else:
            opponent_name = opp_cell.get_text(strip=True)
            opponent_team_id = None

        games.append(GameRecord(
            team_id=meta.team_id,
            team_name=meta.team_name,
            date=date,
            event=current_event,
            result=result,
            score=score_text,
            score_us=score_us,
            score_them=score_them,
            opponent_name=opponent_name,
            opponent_team_id=opponent_team_id,
        ))

    return games
