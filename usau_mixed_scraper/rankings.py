"""
USAU Club Rankings Algorithm v2.0 implementation.

Sources:
  https://play.usaultimate.org/teams/events/rankings/  (algorithm description)
  Formula images: /assets/1/7/Algo.PNG, Algo2.PNG, Algo3.PNG

Key formulas
------------
Rating differential x (per game):
    r = L / (W - 1)
    x = 125 + 475 * sin(min(1, (1-r)/0.5) * 0.4π) / sin(0.4π)

Score weight:
    score_weight = min(1, sqrt((W + max(L, floor((W-1)/2))) / 19))
    → always 1.0 when W >= 13 or W+L >= 19

Date weight (exponential interpolation):
    t = days_elapsed / season_total_days   (0 at start, 1 at end)
    date_weight = 0.5 * 2^t               (0.5 first week → 1.0 last week)

Game rating (from team T's perspective):
    win:  game_rating = opponent_rating + x
    loss: game_rating = opponent_rating - x

Team rating = weighted average of all game ratings
    weight = score_weight * date_weight

Iteration: start all teams at 1000, recompute thousands of times until convergence.

Blowout ignore rule: a game is ignored if ALL of:
    1. winner's rating > loser's rating + 600
    2. winning score > 2 * losing score
    3. winner has >= 5 other valid (non-ignored) results
"""

import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path


# USAU 2026 Club regular season boundary (last weekend before Series begins)
SEASON_END_DEFAULT = date(2026, 9, 7)


# ---------------------------------------------------------------------------
# Core formula functions
# ---------------------------------------------------------------------------

def rating_differential(w: int, l: int) -> float:
    """
    USAU rating differential x for a game with winning score w and losing
    score l.

    x = 125 + 475 * sin(min(1, (1-r)/0.5) * 0.4π) / sin(0.4π)
    r = l / (w - 1)
    """
    if w <= l:
        raise ValueError(f"Winning score ({w}) must exceed losing score ({l})")
    r = 0.0 if w == 1 else l / (w - 1)
    t = min(1.0, (1.0 - r) / 0.5)
    return 125.0 + 475.0 * math.sin(t * 0.4 * math.pi) / math.sin(0.4 * math.pi)


def score_weight(w: int, l: int) -> float:
    """
    Score weight = min(1, sqrt((W + max(L, floor((W-1)/2))) / 19)).

    Equals 1.0 when W >= 13 or W+L >= 19.
    """
    effective_total = w + max(l, (w - 1) // 2)
    return min(1.0, math.sqrt(effective_total / 19.0))


def date_weight(game_date: date, season_start: date, season_end: date) -> float:
    """
    Exponential interpolation: 0.5 at season start, 1.0 at season end.
    date_weight = 0.5 * 2^t  where t = fraction of season elapsed.
    """
    total = (season_end - season_start).days
    if total <= 0:
        return 1.0
    elapsed = max(0, (game_date - season_start).days)
    t = min(1.0, elapsed / total)
    return 0.5 * (2.0 ** t)


# ---------------------------------------------------------------------------
# Rankings computation
# ---------------------------------------------------------------------------

def compute_rankings(
    games_csv: str | Path,
    season_start: date | None = None,
    season_end: date | None = None,
    iterations: int = 2000,
    blowout_ignore: bool = True,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """
    Compute USAU power ratings for all teams in games_csv.

    Returns a 2-tuple:
      rankings  — list of dicts sorted by rating (descending):
                    rank, team_id, team_name, rating, wins, losses,
                    games_counted, games_ignored
      breakdown — dict[team_id → list of per-game contribution dicts]
                    Each entry carries: opponent_name, opponent_team_id,
                    result, score_us, score_them, opponent_rating, x,
                    score_weight, date_weight, weight, weight_share,
                    game_rating, impact, ignored, date, event.
    """
    games_csv = Path(games_csv)
    records = _load_games(games_csv)

    if not records:
        return []

    game_dates = [r["date"] for r in records]
    if season_start is None:
        season_start = min(game_dates)
    if season_end is None:
        season_end = SEASON_END_DEFAULT

    # Pre-compute static weights (only x changes with ratings, not score/date weights)
    for r in records:
        r["sw"] = score_weight(r["w_s"], r["l_s"])
        r["dw"] = date_weight(r["date"], season_start, season_end)
        r["x"] = rating_differential(r["w_s"], r["l_s"])
        r["base_weight"] = r["sw"] * r["dw"]

    # Index: team → [(record_idx, is_winner)]
    team_games: dict[str, list[tuple[int, bool]]] = defaultdict(list)
    for i, r in enumerate(records):
        team_games[r["w_id"]].append((i, True))
        team_games[r["l_id"]].append((i, False))

    all_teams = set(team_games)
    ratings = {tid: 1000.0 for tid in all_teams}

    for _ in range(iterations):
        ignored = _blowout_ignored(records, ratings) if blowout_ignore else [False] * len(records)

        new_ratings: dict[str, float] = {}
        for tid in all_teams:
            num = den = 0.0
            for idx, is_winner in team_games[tid]:
                if ignored[idx]:
                    continue
                rec = records[idx]
                opp = rec["l_id"] if is_winner else rec["w_id"]
                opp_rating = ratings[opp]
                gr = opp_rating + rec["x"] if is_winner else opp_rating - rec["x"]
                w = rec["base_weight"]
                num += gr * w
                den += w
            new_ratings[tid] = num / den if den else 1000.0

        max_change = max(abs(new_ratings[tid] - ratings[tid]) for tid in all_teams)
        ratings = new_ratings
        if max_change < 0.001:
            break

    ignored = _blowout_ignored(records, ratings) if blowout_ignore else [False] * len(records)

    # Tally wins/losses and SOS (counting only non-ignored games)
    stats: dict[str, dict] = {
        tid: {"wins": 0, "losses": 0, "counted": 0, "ignored": 0, "sos_sum": 0.0}
        for tid in all_teams
    }
    for i, rec in enumerate(records):
        if ignored[i]:
            stats[rec["w_id"]]["ignored"] += 1
            stats[rec["l_id"]]["ignored"] += 1
        else:
            stats[rec["w_id"]]["wins"] += 1
            stats[rec["w_id"]]["counted"] += 1
            stats[rec["w_id"]]["sos_sum"] += ratings[rec["l_id"]]
            stats[rec["l_id"]]["losses"] += 1
            stats[rec["l_id"]]["counted"] += 1
            stats[rec["l_id"]]["sos_sum"] += ratings[rec["w_id"]]

    # Build output
    result = []
    for tid in all_teams:
        s = stats[tid]
        sos = round(s["sos_sum"] / s["counted"], 1) if s["counted"] else None
        result.append({
            "team_id": tid,
            "team_name": _team_names.get(tid, tid),
            "rating": round(ratings[tid], 1),
            "wins": s["wins"],
            "losses": s["losses"],
            "games_counted": s["counted"],
            "games_ignored": s["ignored"],
            "sos": sos,
        })

    result.sort(key=lambda r: -r["rating"])
    for i, r in enumerate(result):
        r["rank"] = i + 1

    # ------------------------------------------------------------------ #
    # Build per-game breakdown for team detail pages.
    #
    # Uses leave-one-out (LOO) attribution: hold opponents' ratings fixed
    # and compute what the team rating would be without each individual game.
    #   impact_i = rating(T) − rating_without_i
    # Positive impact  → game pulled the rating UP   (▲ green in the UI)
    # Negative impact  → game pulled the rating DOWN (▼ red in the UI)
    # Note: opponents' ratings are not re-solved when a game is excluded,
    # so this is an approximation — the standard approach for this style of
    # attribution and consistent with how the weights were computed.
    # ------------------------------------------------------------------ #
    breakdown: dict[str, list[dict]] = {}
    for tid in all_teams:
        # First pass: accumulate weighted sums over counted games
        counted: list[tuple[dict, float, float]] = []  # (info, game_rating, weight)
        ign_games: list[dict] = []
        s_num = s_den = 0.0

        for idx, is_winner in team_games[tid]:
            rec = records[idx]
            opp_id = rec["l_id"] if is_winner else rec["w_id"]
            opp_rating = ratings[opp_id]
            gr = opp_rating + rec["x"] if is_winner else opp_rating - rec["x"]
            w = rec["base_weight"]
            d = rec["date"]
            info: dict = {
                "opponent_name": _team_names.get(opp_id, opp_id),
                "opponent_team_id": opp_id,
                "result": "win" if is_winner else "loss",
                "score_us": rec["w_s"] if is_winner else rec["l_s"],
                "score_them": rec["l_s"] if is_winner else rec["w_s"],
                "opponent_rating": round(opp_rating, 1),
                "x": round(rec["x"], 1),
                "score_weight": round(rec["sw"], 4),
                "date_weight": round(rec["dw"], 4),
                "weight": round(w, 4),
                "game_rating": round(gr, 1),
                "date": f"{d.strftime('%B')} {d.day}",
                "iso_date": d.isoformat(),
                "event": rec["event"],
                "ignored": ignored[idx],
            }
            if ignored[idx]:
                info["weight_share"] = None
                info["impact"] = None
                ign_games.append(info)
            else:
                s_num += gr * w
                s_den += w
                counted.append((info, gr, w))

        # Second pass: compute LOO impact for each counted game.
        # Anchored LOO: hold opponent ratings fixed and anchor to the OFFICIAL
        # stored team rating so that impacts are always consistent with the
        # displayed rating even when the blowout-ignore set shifts at the
        # convergence boundary.
        #   impact_i = w_i * (game_rating_i − R_T) / (Σw − w_i)
        # Positive impact → game pulled the rating UP (game_rating > team rating)
        # Negative impact → game pulled the rating DOWN (game_rating < team rating)
        team_rating = ratings[tid]
        games_out: list[dict] = []
        for info, gr, w in counted:
            ws = (w / s_den * 100) if s_den else 0.0
            info["weight_share"] = round(ws, 2)
            rem_den = s_den - w
            if rem_den > 0:
                impact = w * (gr - team_rating) / rem_den
            else:
                impact = 0.0
            info["impact"] = round(impact, 1)
            games_out.append(info)

        # Sort by |impact| descending; ignored games go at the end
        games_out.sort(key=lambda g: -abs(g["impact"]))
        breakdown[tid] = games_out + ign_games

    return result, breakdown


# ---------------------------------------------------------------------------
# Blowout ignore rule
# ---------------------------------------------------------------------------

def _blowout_ignored(records: list[dict], ratings: dict[str, float]) -> list[bool]:
    """
    Mark games as ignored if:
      1. winner rated >600 pts higher than loser
      2. winning score > 2 * losing score
      3. winner has >= 5 other non-ignored results (simplified: other games not
         also meeting conditions 1+2 for the winner)
    """
    ignored = [False] * len(records)

    # Determine per-game which meet conditions 1+2
    blowout_candidates = []
    for i, rec in enumerate(records):
        w_r = ratings.get(rec["w_id"], 1000.0)
        l_r = ratings.get(rec["l_id"], 1000.0)
        if w_r - l_r > 600 and rec["w_s"] > 2 * rec["l_s"]:
            blowout_candidates.append(i)

    # For each candidate, check condition 3
    for i in blowout_candidates:
        w_id = records[i]["w_id"]
        other_valid = sum(
            1 for j, r in enumerate(records)
            if j != i
            and (r["w_id"] == w_id or r["l_id"] == w_id)
            and j not in blowout_candidates
        )
        if other_valid >= 5:
            ignored[i] = True

    return ignored


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

_team_names: dict[str, str] = {}


def _load_games(path: Path) -> list[dict]:
    global _team_names
    _team_names = {}
    records = []

    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("score_a") or not row.get("score_b"):
                continue
            try:
                sa, sb = int(row["score_a"]), int(row["score_b"])
            except ValueError:
                continue
            if sa == sb:
                continue
            a_id = row.get("team_a_id", "").strip()
            b_id = row.get("team_b_id", "").strip()
            if not a_id or not b_id:
                continue

            _team_names[a_id] = row.get("team_a", a_id)
            _team_names[b_id] = row.get("team_b", b_id)

            # Parse "Month DD" date strings (season year is always 2026)
            try:
                game_date = datetime.strptime(row["date"].strip() + " 2026", "%B %d %Y").date()
            except ValueError:
                continue

            if sa > sb:
                w_id, l_id, w_s, l_s = a_id, b_id, sa, sb
            else:
                w_id, l_id, w_s, l_s = b_id, a_id, sb, sa

            records.append({
                "w_id": w_id, "l_id": l_id,
                "w_s": w_s, "l_s": l_s,
                "event": row.get("event", ""),
                "date": game_date,
            })

    return records


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def write_rankings_json(rankings: list[dict], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rankings, f, indent=2)


def write_breakdown_json(breakdown: dict[str, list[dict]], path: str | Path) -> None:
    """Write per-game contribution breakdown to breakdown.json."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(breakdown, f, indent=2)


# ---------------------------------------------------------------------------
# Region / section lookup (derived from city_state since USAU doesn't expose
# region or section fields on individual team pages)
# ---------------------------------------------------------------------------

_STATE_REGION: dict[str, str] = {
    "Alaska": "Northwest",    "Idaho": "Northwest",    "Montana": "Northwest",
    "Oregon": "Northwest",    "Washington": "Northwest", "Wyoming": "Northwest",
    "Arizona": "Southwest",   "California": "Southwest", "Hawaii": "Southwest",
    "Nevada": "Southwest",
    "Colorado": "South Central", "Kansas": "South Central", "New Mexico": "South Central",
    "Oklahoma": "South Central", "Texas": "South Central", "Utah": "South Central",
    "Iowa": "North Central",  "Minnesota": "North Central", "Missouri": "North Central",
    "Nebraska": "North Central", "North Dakota": "North Central", "South Dakota": "North Central",
    "Illinois": "Great Lakes", "Indiana": "Great Lakes",
    "Michigan": "Great Lakes", "Wisconsin": "Great Lakes",
    "Kentucky": "Ohio Valley", "Ohio": "Ohio Valley",
    "Pennsylvania": "Ohio Valley", "West Virginia": "Ohio Valley",
    "Delaware": "Mid-Atlantic", "District of Columbia": "Mid-Atlantic",
    "Maryland": "Mid-Atlantic", "New Jersey": "Mid-Atlantic", "New York": "Mid-Atlantic",
    "Connecticut": "New England", "Maine": "New England",  "Massachusetts": "New England",
    "New Hampshire": "New England", "Rhode Island": "New England", "Vermont": "New England",
    "North Carolina": "Atlantic Coast", "Virginia": "Atlantic Coast",
    "Alabama": "Southeast",  "Arkansas": "Southeast",  "Florida": "Southeast",
    "Georgia": "Southeast",  "Louisiana": "Southeast", "Mississippi": "Southeast",
    "South Carolina": "Southeast", "Tennessee": "Southeast",
}

_STATE_SECTION: dict[str, str] = {
    "Washington": "Cascades", "Oregon": "Cascades",
    "Alaska": "Pines", "Idaho": "Pines", "Montana": "Pines", "Wyoming": "Pines",
    "Florida": "Florida",
}


def region_from_city_state(city_state: str) -> tuple[str, str]:
    """
    Return (region, section) for a "City, State" string.
    State name is the portion after the last ', '.
    Falls back to ("", "") when the state is unrecognised.
    """
    if not city_state or "," not in city_state:
        return "", ""
    state = city_state.rsplit(",", 1)[-1].strip()
    region = _STATE_REGION.get(state, "")
    if not region:
        return "", ""
    section = _STATE_SECTION.get(state, region)
    return region, section


def merge_team_metadata(rankings: list[dict], teams_csv: str | Path) -> None:
    """
    Augment each ranking entry in-place with region, section, and city_state
    by joining against teams.csv on team_id.
    """
    teams_csv = Path(teams_csv)
    if not teams_csv.exists():
        return
    lookup: dict[str, dict] = {}
    with open(teams_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tid = row.get("team_id", "").strip()
            if tid:
                lookup[tid] = row
    for entry in rankings:
        row = lookup.get(entry.get("team_id", ""))
        city_state = row.get("city_state", "") if row else ""
        region, section = region_from_city_state(city_state)
        entry["city_state"] = city_state
        entry["region"] = region
        entry["section"] = section
