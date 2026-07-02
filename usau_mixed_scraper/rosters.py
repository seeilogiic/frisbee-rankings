"""
Scrape tournament-specific player rosters for Club teams in a given season/division.

This module traverses from each team's season page to the homepages of the
tournaments they played, submits the division-specific WebForm postback, retrieves
their tournament-specific registration (EventTeamId), parses the player roster
registered for that specific event, and writes the results to a CSV file.

Stable identifiers (team_key and player_key) are computed to track players and
teams across seasons and teams.
"""

import argparse
import csv
import logging
import os
import re
import sys
import urllib.parse
from datetime import datetime

from .client import USAUClient
from .teams import enumerate_teams

logger = logging.getLogger(__name__)

# Season and Division configuration mapping
_SEASON_ID_MAP = {
    2027: "22#2027",
    2026: "21#2026",
    2025: "20#2025",
    2024: "19#2024",
    2023: "18#2023",
    2022: "17#2022",
    2021: "16#2021",
    2020: "15#2020",
    2019: "14#2019",
    2018: "13#2018",
}

_DIVISION_MAP = {
    "mixed": "1#mixed",
    "mens": "17#men",
    "womens": "2#women",
}

ROSTER_COLUMNS = [
    "season",
    "division",
    "tournament_name",
    "team_name",
    "team_city_state",
    "stable_team_key",
    "player_name",
    "stable_player_key",
    "jersey",
    "position",
    "height",
]


def normalize_key(name: str) -> str:
    """Normalize names into stable lowercase alphanumeric identifiers."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def build_team_key(team_name: str, city_state: str, division: str) -> str:
    """Create a globally unique, stable team key across years."""
    norm_name = normalize_key(team_name)
    norm_city = normalize_key(city_state)
    return f"{norm_name}_{norm_city}_{division}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape tournament-specific player rosters for Club teams."
    )
    parser.add_argument("--season", type=int, default=2026, help="Season year (default: 2026)")
    parser.add_argument(
        "--division",
        choices=["mixed", "mens", "men", "womens", "women"],
        required=True,
        help="Gender division to scrape: mixed, mens/men, womens/women",
    )
    parser.add_argument("--out", default="./out", help="Output directory (default: ./out)")
    parser.add_argument("--cache", default="./cache", help="Cache directory (default: ./cache)")
    parser.add_argument("--no-cache", action="store_true", help="Disable HTTP cache")
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N teams (0 = all)")
    parser.add_argument("--delay", type=float, default=1.5, help="Base request delay (seconds)")
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    season_id = _SEASON_ID_MAP.get(args.season)
    if season_id is None:
        logging.error(
            "Unknown season %d. Supported years: %s",
            args.season,
            sorted(_SEASON_ID_MAP),
        )
        return 1

    # Normalize division name
    norm_division = args.division
    if norm_division == "men":
        norm_division = "mens"
    elif norm_division == "women":
        norm_division = "womens"

    gender_division = _DIVISION_MAP.get(norm_division, "1#mixed")
    division_display = norm_division.capitalize()

    # Determine out directory folder
    out_dir = args.out
    if out_dir == "./out":
        out_dir = os.path.join(args.out, norm_division, str(args.season))
    os.makedirs(out_dir, exist_ok=True)
    rosters_csv_path = os.path.join(out_dir, "tournament_rosters.csv")

    cache_dir = None if args.no_cache else args.cache
    client = USAUClient(cache_dir=cache_dir, delay=args.delay)

    # 1. Enumerate all season teams
    teams = enumerate_teams(client, season_id=season_id, gender_division=gender_division)
    if args.limit > 0:
        logging.info("--limit %d: restricting to first %d teams.", args.limit, args.limit)
        teams = teams[: args.limit]

    # Deduplication set of (tournament, team_key) to avoid duplicate scraping
    scraped_combinations = set()
    roster_rows = []
    errors = []

    # 2. Iterate and scrape
    for i, team in enumerate(teams, start=1):
        logging.info("[%d/%d] Scraping season profile for %s …", i, len(teams), team.name)
        try:
            soup = client.get(team.team_path)

            # Gather city/state metadata to build stable team key
            def _dd(id_):
                tag = soup.find(id=id_)
                return tag.get_text(strip=True) if tag else ""

            city_state = _dd("CT_Main_0_ucTeamDetails_dlCity")
            stable_key = build_team_key(team.name, city_state, norm_division)

            # Find tournaments from schedule table
            schedule_table = soup.find(id="CT_Right_0_gvEventScheduleScores")
            if not schedule_table:
                logging.info("  No schedule table found for %s", team.name)
                continue

            tournaments = []
            for row in schedule_table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) == 1:
                    a = cells[0].find("a")
                    if a and "/events/" in a.get("href"):
                        tournaments.append({
                            "name": a.get_text(strip=True),
                            "path": a.get("href"),
                        })

            if not tournaments:
                logging.info("  No tournaments found in schedule for %s", team.name)
                continue

            logging.info("  Found %d tournament(s) in schedule", len(tournaments))

            for t in tournaments:
                tourn_name = t["name"]
                tourn_path = t["path"]
                combo_key = (tourn_name, stable_key)

                if combo_key in scraped_combinations:
                    logging.debug("  Already scraped %s at %s. Skipping.", team.name, tourn_name)
                    continue

                logging.info("  Processing tournament: %s …", tourn_name)

                # Fetch tournament homepage
                tourn_soup = client.get(tourn_path)

                # Find the division schedule postback submit button (e.g. Mixed Division Schedule)
                target_btn = None
                for dl in tourn_soup.find_all("dl", class_="groupType"):
                    text = dl.get_text()
                    if f"{division_display} Division Schedule" in text:
                        btn = dl.find("input", type="submit")
                        if btn:
                            target_btn = btn.get("name")
                            break

                if not target_btn:
                    logging.warning("    Could not find submit button for %s division.", division_display)
                    continue

                logging.debug("    Found submit button '%s'. Requesting division page…", target_btn)
                div_soup = client.post(tourn_path, {target_btn: "Club"})

                # Locate the team's EventTeamId link
                event_team_id = None
                for a in div_soup.find_all("a", href=re.compile(r"/events/teams/\?EventTeamId=", re.I)):
                    link_text = a.get_text(strip=True)
                    clean_link_text = re.sub(r"\s*\(\d+\)\s*$", "", link_text)  # Strip seed numbers
                    if clean_link_text.lower() == team.name.lower():
                        href = a.get("href")
                        parsed = urllib.parse.urlparse(href)
                        params = urllib.parse.parse_qs(parsed.query)
                        event_team_id = params.get("EventTeamId", [None])[0]
                        break

                if not event_team_id:
                    logging.warning("    Could not find EventTeamId registration link for %s.", team.name)
                    continue

                logging.debug("    Found EventTeamId: %s", event_team_id)

                # Fetch the tournament-specific roster details
                roster_path = f"/events/teams/?EventTeamId={urllib.parse.quote(event_team_id)}"
                roster_soup = client.get(roster_path)

                # Parse the player list
                roster_table = roster_soup.find(id="CT_Main_0_ucTeamDetails_gvList")
                if not roster_table:
                    logging.warning("    Roster table not found on page.")
                    continue

                rows = roster_table.find_all("tr")
                for row in rows[1:]:
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 5:
                        jersey = cells[0].get_text(strip=True)
                        player_name = cells[1].get_text(strip=True)
                        position = cells[3].get_text(strip=True)
                        height = cells[4].get_text(strip=True)

                        stable_player_key = normalize_key(player_name)

                        roster_rows.append({
                            "season": args.season,
                            "division": norm_division,
                            "tournament_name": tourn_name,
                            "team_name": team.name,
                            "team_city_state": city_state,
                            "stable_team_key": stable_key,
                            "player_name": player_name,
                            "stable_player_key": stable_player_key,
                            "jersey": jersey,
                            "position": position,
                            "height": height,
                        })

                scraped_combinations.add(combo_key)
                logging.info("    Scraped %d roster rows.", len(rows) - 1)

        except Exception as exc:
            logging.warning("  ERROR scraping %s: %s", team.name, exc)
            errors.append((team.name, str(exc)))

    # 3. Export CSV
    if roster_rows:
        with open(rosters_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=ROSTER_COLUMNS)
            writer.writeheader()
            writer.writerows(roster_rows)
        logging.info("=" * 60)
        logging.info("Export completed successfully!")
        logging.info("Roster rows scraped: %d", len(roster_rows))
        logging.info("Output file written: %s", rosters_csv_path)
    else:
        logging.info("=" * 60)
        logging.info("No roster rows scraped.")

    if errors:
        logging.warning("Errors:")
        for name, msg in errors:
            logging.warning("  %s: %s", name, msg)

    return 0


if __name__ == "__main__":
    sys.exit(main())
