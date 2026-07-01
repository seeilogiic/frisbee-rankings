"""
Enumerate every Club-Mixed team registered for a given season.

Source: POST /teams/events/rankings/ with the Mixed + Club + SeasonId filter.
The GridView pager is followed automatically until all pages are exhausted.

Pagination mechanics
--------------------
The pager row at the bottom of the result table is a plain HTML <td> that
contains anchor tags whose href is a javascript:__doPostBack(target, '') call.
Page-number links carry a sequential target ending in $ctl23$ctl00$ctlN
(N=0..9 maps to pages 2..11 in the current window); the "Next 20 »" link at
the end of the window advances the window.  We parse these links from each
response and follow the "Next 20 »" link until it disappears.
"""

import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Iterator

from .client import USAUClient

logger = logging.getLogger(__name__)

TEAMS_PATH = "/teams/events/rankings/"


@dataclass
class TeamRef:
    """Lightweight reference to a team page."""
    name: str
    team_id: str       # URL-decoded TeamId parameter
    team_path: str     # relative path, e.g. /teams/events/Eventteam/?TeamId=...


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enumerate_teams(
    client: USAUClient,
    season_id: str = "21#2026",
    gender_division: str = "1#mixed",
    competition_level: str = "club",
) -> list[TeamRef]:
    """
    Return a deduplicated list of TeamRef objects for all Club-Mixed teams
    registered in the given season, sourced from the team directory.
    """
    teams: dict[str, TeamRef] = {}  # keyed by team_id

    logger.info("Enumerating teams from directory (%s)…", TEAMS_PATH)
    for t in _iter_directory(client, season_id, gender_division, competition_level):
        teams[t.team_id] = t
    logger.info("  Directory: %d teams found", len(teams))

    result = sorted(teams.values(), key=lambda t: t.name.lower())
    logger.info("Total unique Club-Mixed teams: %d", len(result))
    return result


# ---------------------------------------------------------------------------
# Directory enumeration (paginated)
# ---------------------------------------------------------------------------

# Fields that accompany the search-button POST (filter form).
# These are the static nav-state hidden fields the page always expects.
_NAV_FIELDS = {
    "CT_Header$CC_TopNav$rptNav$ctl00$ccSubNav$hdn": "6",
    "CT_Header$CC_TopNav$rptNav$ctl01$ccSubNav$hdn": "25",
    "CT_Header$CC_TopNav$rptNav$ctl02$ccSubNav$hdn": "144",
    "CT_Header$CC_TopNav$rptNav$ctl03$ccSubNav$hdn": "26",
    "CT_Header$CC_TopNav$rptNav$ctl04$ccSubNav$hdn": "27",
    "CT_Header$CC_TopNav$rptNav$ctl05$ccSubNav$hdn": "28",
}


def _filter_fields(season_id: str, gender_division: str, competition_level: str) -> dict:
    return {
        **_NAV_FIELDS,
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "CT_Main_0$F_TeamName": "",
        "CT_Main_0$F_SchoolName": "",
        "CT_Main_0$F_GenderDivisionId": gender_division,
        "CT_Main_0$F_CompetitionLevelId": competition_level,
        "CT_Main_0$F_CompetitionDivisionId": "",
        "CT_Main_0$F_StateId": "",
        "CT_Main_0$F_Designation": "",
        "CT_Main_0$F_SeasonId": season_id,
        "CT_Main_0$F_Status": "",
        "CT_Main_0$btnSubmit": "Submit",
    }


def _iter_directory(
    client: USAUClient,
    season_id: str,
    gender_division: str,
    competition_level: str,
) -> Iterator[TeamRef]:
    """Yield TeamRef objects from every page of the team directory."""
    # --- Initial GET to prime the viewstate ---
    client.get(TEAMS_PATH)

    # --- First POST: submit the filter form ---
    filter_data = _filter_fields(season_id, gender_division, competition_level)
    soup = client.post(TEAMS_PATH, filter_data)

    # Verify the filter took effect by checking the total-rows indicator.
    total_rows = _parse_total_rows(soup)
    logger.info("  Directory filter active: %s total rows", total_rows or "unknown")

    page_num = 1
    while True:
        teams_on_page = list(_parse_team_links(soup))
        logger.debug("  Page %d: %d teams", page_num, len(teams_on_page))
        yield from teams_on_page

        # --- Find "Next 20" or any unvisited page links ---
        next_target = _find_next_pager_target(soup)
        if next_target is None:
            break

        # IMPORTANT: pager POSTs must re-include all filter dropdown values.
        # ASP.NET does NOT save dropdown selections in ViewState; a full form
        # submit is always required.  Only __EVENTTARGET changes between pages.
        pager_data = {
            **_NAV_FIELDS,
            "__EVENTTARGET": next_target,
            "__EVENTARGUMENT": "",
            "CT_Main_0$F_TeamName": "",
            "CT_Main_0$F_SchoolName": "",
            "CT_Main_0$F_GenderDivisionId": gender_division,
            "CT_Main_0$F_CompetitionLevelId": competition_level,
            "CT_Main_0$F_CompetitionDivisionId": "",
            "CT_Main_0$F_StateId": "",
            "CT_Main_0$F_Designation": "",
            "CT_Main_0$F_SeasonId": season_id,
            "CT_Main_0$F_Status": "",
            # NOTE: do NOT include btnSubmit here; the pager link is the event
        }
        soup = client.post(TEAMS_PATH, pager_data)
        page_num += 1


def _parse_team_links(soup) -> Iterator[TeamRef]:
    """Extract TeamRef objects from a team-directory result page."""
    # Team links have ids matching lnkTeam (the existing scraper's pattern)
    for tag in soup.find_all("a", id=re.compile(r"lnkTeam", re.I)):
        href = tag.get("href", "")
        name = tag.get_text(strip=True)
        if not href or not name:
            continue
        team_id = _extract_team_id(href)
        if team_id:
            yield TeamRef(name=name, team_id=team_id, team_path=href)


def _parse_total_rows(soup) -> str | None:
    """Extract e.g. '1 - 20 of 257' from the pager row."""
    m = re.search(r"Rows:</b>\s*([\d\s\-of]+)</nobr>", str(soup))
    return m.group(1).strip() if m else None


def _parse_page_info(soup) -> tuple[int, int]:
    """
    Return (current_page, total_pages) by parsing 'Page: X of Y' from the
    pager row.  Falls back to (1, 1) if not found (treat as single-page).
    """
    raw = str(soup)
    m = re.search(r"Page:</b>\s*(\d+)\s*of\s*(\d+)", raw)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1, 1


def _find_next_pager_target(soup) -> str | None:
    """
    Return the __doPostBack target for advancing to the next page, or None if
    we are already on the last page.

    We check "Page: X of Y": if X == Y we are done.
    Otherwise we look for the "Next N »" window-advance link first; failing
    that we look for any numbered page link > current page.

    The pager link hrefs use HTML-escaped single-quotes (&#39;) so we must
    use BeautifulSoup's href attribute (which is already unescaped).
    """
    cur_page, total_pages = _parse_page_info(soup)
    logger.debug("  Pager: page %d of %d", cur_page, total_pages)

    if cur_page >= total_pages:
        return None  # Last page

    # Prefer "Next N" window-advance link (text starts/contains "Next")
    for a in soup.find_all("a", href=re.compile(r"__doPostBack", re.I)):
        text = a.get_text(strip=True).lower()
        if text.startswith("next"):
            href = a.get("href", "")
            m = re.search(r"__doPostBack\('([^']+)'", href)
            if m:
                return m.group(1)

    # Fall back to first numbered page link > current page
    for a in soup.find_all("a", href=re.compile(r"__doPostBack", re.I)):
        text = a.get_text(strip=True)
        if re.match(r"^\d+$", text):
            try:
                if int(text) > cur_page:
                    href = a.get("href", "")
                    m = re.search(r"__doPostBack\('([^']+)'", href)
                    if m:
                        return m.group(1)
            except ValueError:
                continue

    return None  # No forward link found


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _extract_team_id(href: str) -> str | None:
    """Extract the URL-decoded TeamId from a /teams/events/Eventteam/ href."""
    m = re.search(r"[Tt]eam[Ii]d=([^&]+)", href)
    if m:
        return urllib.parse.unquote(m.group(1))
    return None
