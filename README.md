# frisbee-rankings

Scrapes all completed games played by every Club-Mixed team in a given USA Ultimate season, computes USAU v2.0 power rankings, and serves an interactive viewer.

## Quick start

```bash
pip install -r requirements.txt

# 1. Scrape all games + compute rankings
python -m usau_mixed_scraper --compute-rankings

# 2. Open the viewer
python serve.py            # rankings leaderboard
python serve.py graph      # game connection graph
```

`serve.py` starts a local HTTP server and prints the URLs — Ctrl+click either one to open it. The pages auto-load from `out/` — just click **Refresh** after re-running the scraper.

## Viewer pages

| Page | URL | Description |
|------|-----|-------------|
| `rankings.html` | `http://localhost:8765/rankings.html` | Sortable power-rankings leaderboard with rating bars and W-L records |
| `graph.html`    | `http://localhost:8765/graph.html`    | Force-directed game connection graph — shows which teams are linked through shared opponents |

## Scraper output

| File | Description |
|------|-------------|
| `out/games.csv`      | One row per unique completed game (date, event, both teams + IDs, scores, winner) |
| `out/teams.csv`      | One row per team (metadata + season W/L totals) |
| `out/rankings.json`  | USAU v2.0 power ratings, sorted by rank |

## Scraper flags

| Flag | Default | Notes |
|------|---------|-------|
| `--season YEAR` | 2026 | Season years 2018–2027 are mapped to their site IDs |
| `--out DIR` | `./out` | Output directory |
| `--cache DIR` | `./cache` | On-disk HTTP response cache — makes re-runs instant for already-fetched pages |
| `--no-cache` | off | Disable cache (always re-fetch) |
| `--limit N` | 0 (all) | Scrape only the first N teams — useful for quick tests |
| `--delay SECS` | 1.5 | Base inter-request delay (actual adds 0.2–0.8 s jitter) |
| `--compute-rankings` | off | Compute power ratings after scraping and write `out/rankings.json` |
| `--rankings-only` | off | Skip scraping; recompute rankings from an existing `out/games.csv` |
| `--verbose` | off | Enable DEBUG-level logging |

### Rankings-only workflow

If `out/games.csv` already exists (e.g. you want to tweak the algorithm):

```bash
python -m usau_mixed_scraper --rankings-only
python serve.py  # then click Refresh in the browser
```

## Rankings algorithm

USAU Power Rankings v2.0 (last updated 1/12/2018). Each game produces a rating differential *x*:

```
r = L / (W − 1)
x = 125 + 475 × sin(min(1, (1−r)/0.5) × 0.4π) / sin(0.4π)
```

A team's rating is the weighted average of `opponent_rating ± x` across all games, where:

- **Score weight** = `min(1, √((W + max(L, ⌊(W−1)/2⌋)) / 19))` — low-scoring games count less
- **Date weight** = `0.5 × 2^t` — recent games count more (t = fraction of season elapsed)

All teams initialize at 1000 and the system iterates to convergence. Games where the winner is rated >600 pts higher, won by more than 2×, and has ≥5 other results are excluded (blowout rule).

> **Early-season caveat:** ratings are only meaningful once the game graph is well-connected. In June, many teams play in isolated tournament clusters with no common opponents — absolute rating values will diverge, but relative rankings within a cluster are still informative.

## How it works

### Site structure

All data lives on **`play.usaultimate.org`**, an ASP.NET WebForms site:

| Page | Purpose |
|------|---------|
| `/teams/events/rankings/` | Team directory with filter form |
| `/teams/events/Eventteam/?TeamId=<id>` | Individual team schedule table |

### Team discovery

The directory form requires a POST with `CT_Main_0$F_GenderDivisionId=1#mixed`, `CT_Main_0$F_CompetitionLevelId=club`, `CT_Main_0$F_SeasonId=21#2026`, and `CT_Main_0$btnSubmit=Submit`. Results are paginated 20/page; all filter fields must be re-included on every pager POST (ASP.NET doesn't persist dropdown state in ViewState).

### Schedule parsing

Each team's `CT_Right_0_gvEventScheduleScores` table has single-cell tournament-header rows and multi-cell game rows. Only rows with a numeric `N – N` score are kept. Win/loss is read from the score cell's CSS class.

### Deduplication

Each game appears on both teams' schedule pages. The canonical key is `frozenset({team_id, opponent_team_id})` + event name + date + score set.

## Timing and etiquette

- ~257 Club-Mixed teams as of June 2026; ~13 directory pages to enumerate.
- At the default 1.5 s delay, a full run takes approximately **10–12 minutes**.
- The on-disk cache makes re-runs instant for already-fetched pages.
- Only public, non-authenticated competition data is scraped.

## Supported seasons

| `--season` | Site ID |
|------------|---------|
| 2027 | 22 |
| **2026** | **21** (default) |
| 2025 | 20 |
| 2024 | 19 |
| 2023 | 18 |
| 2022 | 17 |
| 2021 | 16 |
| 2020 | 15 |
| 2019 | 14 |
| 2018 | 13 |
