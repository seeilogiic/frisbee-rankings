"""
USA Ultimate Club-Mixed 2026 season scraper — CLI entry point.

Usage
-----
    python -m usau_mixed_scraper [options]

Options
-------
    --season SEASON     Season year, e.g. 2026 (default: 2026).
    --out DIR           Output directory for CSV files (default: ./out).
    --cache DIR         HTTP response cache directory (default: ./cache).
    --no-cache          Disable the on-disk cache (always re-fetch).
    --limit N           Only scrape the first N teams (for quick tests).
    --delay SECS        Base inter-request delay in seconds (default: 1.5).
    --compute-rankings  After scraping, compute power ratings and write
                        <out>/rankings.json  (uses USAU v2.0 algorithm).
    --rankings-only     Skip scraping; just recompute rankings from an
                        existing <out>/games.csv and write rankings.json.
    --verbose           Enable DEBUG-level logging.

Output files
------------
    <out>/games.csv      — one row per unique completed game
    <out>/teams.csv      — one row per team with metadata + win/loss totals
    <out>/rankings.json  — power rankings (when --compute-rankings is set)
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime

from .client import USAUClient
from .export import deduplicate_games, write_games_csv, write_teams_csv, write_metadata
from .schedule import scrape_team
from .teams import enumerate_teams

# Map a 4-digit season year to the SeasonId value used by play.usaultimate.org.
# Source: the F_SeasonId <select> options on /teams/events/rankings/
# e.g. '22#2027', '21#2026', '20#2025', …
_SEASON_ID_MAP: dict[int, str] = {
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape all Club games from USA Ultimate for a given season."
    )
    parser.add_argument("--season", type=int, default=2026, help="Season year (default: 2026)")
    parser.add_argument(
        "--division",
        choices=["mixed", "mens", "men", "womens", "women"],
        required=True,
        help="Gender division to scrape: mixed, mens/men, womens/women"
    )
    parser.add_argument("--out", default="./out", help="Output directory (default: ./out)")
    parser.add_argument("--cache", default="./cache", help="Cache directory (default: ./cache)")
    parser.add_argument("--no-cache", action="store_true", help="Disable HTTP cache")
    parser.add_argument("--limit", type=int, default=0, help="Limit to first N teams (0 = all)")
    parser.add_argument("--delay", type=float, default=1.5, help="Base request delay (seconds)")
    parser.add_argument("--compute-rankings", action="store_true", help="Compute power ratings after scraping")
    parser.add_argument("--rankings-only", action="store_true", help="Skip scraping; recompute rankings from existing games.csv")
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
            "Unknown season %d.  Supported years: %s",
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

    _DIVISION_MAP = {
        "mixed": "1#mixed",
        "mens": "17#men",
        "womens": "2#women",
    }
    gender_division = _DIVISION_MAP.get(norm_division, "1#mixed")

    # Save under division folder if using default output directory and division is not mixed
    out_dir = args.out
    if out_dir == "./out" and norm_division != "mixed":
        out_dir = os.path.join(args.out, norm_division)

    # --rankings-only: skip scraping, just (re)compute from existing CSV
    if getattr(args, "rankings_only", False):
        return _run_rankings_only(args, out_dir, norm_division)

    cache_dir = None if args.no_cache else args.cache
    client = USAUClient(cache_dir=cache_dir, delay=args.delay)

    # ------------------------------------------------------------------ #
    # Step 1: Enumerate all teams
    # ------------------------------------------------------------------ #
    teams = enumerate_teams(client, season_id=season_id, gender_division=gender_division)

    if args.limit > 0:
        logging.info("--limit %d: restricting to first %d teams.", args.limit, args.limit)
        teams = teams[: args.limit]

    # ------------------------------------------------------------------ #
    # Step 2: Scrape each team's schedule
    # ------------------------------------------------------------------ #
    all_games = []
    all_metas = []
    errors = []

    for i, team in enumerate(teams, start=1):
        logging.info("[%d/%d] Scraping %s …", i, len(teams), team.name)
        try:
            meta, games = scrape_team(client, team)
            all_metas.append(meta)
            all_games.extend(games)
            logging.info(
                "  → %d completed game(s) (W%d L%d)",
                len(games), meta.wins, meta.losses,
            )
        except Exception as exc:
            logging.warning("  ERROR scraping %s: %s", team.name, exc)
            errors.append((team.name, str(exc)))

    # ------------------------------------------------------------------ #
    # Step 3: Deduplicate and write CSV
    # ------------------------------------------------------------------ #
    unique_games = deduplicate_games(all_games)

    games_path = f"{out_dir}/games.csv"
    teams_path = f"{out_dir}/teams.csv"
    metadata_path = f"{out_dir}/metadata.json"
    write_games_csv(unique_games, games_path)
    write_teams_csv(all_metas, teams_path)
    write_metadata(
        {
            "scraped_at": datetime.now().isoformat(timespec="seconds"),
            "rankings_computed_at": None,
            "season": args.season,
            "division": norm_division,
            "teams": len(all_metas),
            "games": len(unique_games),
        },
        metadata_path,
    )

    # ------------------------------------------------------------------ #
    # Summary
    # ------------------------------------------------------------------ #
    zero_games = [m.team_name for m in all_metas if m.games_scraped == 0]
    logging.info("=" * 60)
    logging.info("Season       : %d", args.season)
    logging.info("Teams found  : %d", len(teams))
    logging.info("Teams scraped: %d  (errors: %d)", len(all_metas), len(errors))
    logging.info("Total game records (raw):   %d", len(all_games))
    logging.info("Unique games (after dedup): %d", len(unique_games))
    logging.info(
        "Teams with 0 completed games: %d",
        len(zero_games),
    )
    if errors:
        logging.warning("Errors:")
        for name, msg in errors:
            logging.warning("  %s: %s", name, msg)
    logging.info("Output: %s, %s", games_path, teams_path)

    if getattr(args, "compute_rankings", False):
        _run_rankings_only(args, out_dir, norm_division)

    return 0


def _run_rankings_only(args, out_dir: str | None = None, norm_division: str | None = None) -> int:
    from .rankings import compute_rankings, write_rankings_json, write_breakdown_json, merge_team_metadata
    from .export import append_history_snapshot
    
    if norm_division is None:
        norm_division = getattr(args, "division", "mixed")
        if norm_division == "men":
            norm_division = "mens"
        elif norm_division == "women":
            norm_division = "womens"
            
    if out_dir is None:
        out_dir = getattr(args, "out", "./out")
        if out_dir == "./out" and norm_division != "mixed":
            out_dir = os.path.join(out_dir, norm_division)

    games_path = f"{out_dir}/games.csv"
    teams_path = f"{out_dir}/teams.csv"
    rankings_path = f"{out_dir}/rankings.json"
    breakdown_path = f"{out_dir}/breakdown.json"
    metadata_path = f"{out_dir}/metadata.json"
    history_path = f"{out_dir}/history.json"
    if not os.path.exists(games_path):
        logging.error("games.csv not found at %s — run scraper first.", games_path)
        return 1
    logging.info("Computing USAU v2.0 power rankings from %s …", games_path)
    rankings, breakdown = compute_rankings(games_path)
    merge_team_metadata(rankings, teams_path)
    write_rankings_json(rankings, rankings_path)
    write_breakdown_json(breakdown, breakdown_path)
    logging.info("Rankings written to %s  (%d teams rated)", rankings_path, len(rankings))
    logging.info("Breakdown written to %s", breakdown_path)

    # Update metadata: preserve scraped_at if set by a prior full scrape
    meta: dict = {}
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            pass
    meta.setdefault("scraped_at", None)
    computed_at = datetime.now().isoformat(timespec="seconds")
    meta["rankings_computed_at"] = computed_at
    meta.setdefault("season", getattr(args, "season", None))
    meta.setdefault("division", norm_division)
    meta.setdefault("teams", len(rankings))
    meta.setdefault("games", None)
    write_metadata(meta, metadata_path)

    # Append history snapshot (date portion of the computed-at timestamp)
    snapshot_date = computed_at[:10]
    append_history_snapshot(rankings, history_path, snapshot_date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
