from __future__ import annotations

import pytest

from ffagent.config import Config
from ffagent.consultant import Consultant
from ffagent.espn import EspnClient, EspnLeague
from ffagent.sleeper import SleeperClient, SleeperLeague


class FakeHttp:
    """Stands in for JsonHttp: returns canned payloads keyed by URL suffix."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[tuple[str, object, object]] = []

    def get(self, url, params=None, headers=None, cookies=None, cache_ttl=0):
        self.calls.append((url, params, headers))
        for suffix in sorted(self.routes, key=len, reverse=True):
            if url.endswith(suffix):
                return self.routes[suffix]
        if "/projections/nfl/" in url:
            return []  # weeks not in the fixture have no published projections
        raise AssertionError(f"unexpected request {url}")


SCORING = {"pass_yd": 0.04, "pass_td": 4, "rush_yd": 0.1, "rush_td": 6, "rec": 1, "rec_yd": 0.1, "rec_td": 6}


def _proj(pid, pts):
    # A receiving stat line worth `pts` PPR points: pts/2 receptions and pts/2*10 yards.
    return {"player_id": pid, "stats": {"rec": pts / 2, "rec_yd": pts * 5}}


SLEEPER_PLAYERS = {
    "qb1": {"full_name": "Alpha Quarterback", "position": "QB", "team": "KC", "active": True},
    "qb2": {"full_name": "Beta Quarterback", "position": "QB", "team": "BUF", "active": True},
    "rb1": {"full_name": "Carl Runner", "position": "RB", "team": "SF", "active": True},
    "rb2": {"full_name": "Dan Runner", "position": "RB", "team": "DET", "active": True},
    "rb3": {"full_name": "Eli Runner", "position": "RB", "team": "NYJ", "active": True, "injury_status": "Out"},
    "wr1": {"full_name": "Fred Wideout", "position": "WR", "team": "MIA", "active": True},
    "wr2": {"full_name": "Gus Wideout", "position": "WR", "team": "CIN", "active": True},
    "wr3": {"full_name": "Hal Wideout Jr.", "position": "WR", "team": "LAR", "active": True},
    "te1": {"full_name": "Ian Tightend", "position": "TE", "team": "KC", "active": True},
    "te2": {"full_name": "Jon Tightend", "position": "TE", "team": "BAL", "active": True},
    "rb9": {"full_name": "Free Agent Back", "position": "RB", "team": "CHI", "active": True},
    "wr9": {"full_name": "Free Agent Wideout", "position": "WR", "team": "GB", "active": True},
    "old": {"full_name": "Retired Guy", "position": "WR", "team": None, "active": False},
}

WEEKLY = [_proj("qb1", 22), _proj("qb2", 18), _proj("rb1", 16), _proj("rb2", 12), _proj("rb3", 14),
          _proj("wr1", 15), _proj("wr2", 11), _proj("wr3", 13), _proj("te1", 10), _proj("te2", 7),
          _proj("rb9", 13), _proj("wr9", 6), _proj("old", 5)]
SEASON = [{"player_id": p["player_id"], "stats": {k: v * 17 for k, v in p["stats"].items()}} for p in WEEKLY]

SLEEPER_ROUTES = {
    "/state/nfl": {"season": "2026", "season_type": "regular", "week": 7, "display_week": 7},
    "/league/L1": {
        "league_id": "L1", "name": "Test League", "season": "2026", "total_rosters": 2,
        "roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "BN", "BN", "BN"],
        "scoring_settings": SCORING, "settings": {"playoff_week_start": 15},
    },
    "/league/L1/users": [
        {"user_id": "u1", "display_name": "me", "metadata": {"team_name": "My Squad"}},
        {"user_id": "u2", "display_name": "rival", "metadata": {}},
    ],
    "/league/L1/rosters": [
        {"roster_id": 1, "owner_id": "u1", "starters": ["qb1", "rb3", "wr2", "te1", "rb2"],
         "players": ["qb1", "rb2", "rb3", "wr2", "wr3", "te1", "wr1"], "reserve": [],
         "settings": {"wins": 4, "losses": 2, "fpts": 700, "fpts_decimal": 50, "fpts_against": 650}},
        {"roster_id": 2, "owner_id": "u2", "starters": ["qb2", "rb1", "0", "te2", "0"],
         "players": ["qb2", "rb1", "te2"], "reserve": [],
         "settings": {"wins": 2, "losses": 4, "fpts": 720, "fpts_against": 690}},
    ],
    "/league/L1/matchups/7": [
        {"roster_id": 1, "matchup_id": 1, "points": 0},
        {"roster_id": 2, "matchup_id": 1, "points": 0},
    ],
    "/user/me": {"user_id": "u1", "username": "me"},
    "/players/nfl": SLEEPER_PLAYERS,
    "/projections/nfl/2026/7": WEEKLY,
    "/projections/nfl/2026": SEASON,
    "/players/nfl/trending/add": [{"player_id": "rb9", "count": 5000}, {"player_id": "rb1", "count": 100}],
}


@pytest.fixture
def sleeper_league():
    http = FakeHttp(SLEEPER_ROUTES)
    return SleeperLeague("L1", SleeperClient(http), username="me")


@pytest.fixture
def sleeper_consultant(sleeper_league):
    config = Config(season=2026, sleeper_username="me", sleeper_league_id="L1", cache_dir="")
    return Consultant(config, leagues={"sleeper:L1": sleeper_league}, sleeper=sleeper_league.client)


def _espn_player(pid, name, pos_id, pro, week_pts, season_pts, injury="ACTIVE"):
    return {
        "id": pid, "fullName": name, "defaultPositionId": pos_id, "proTeamId": pro, "injuryStatus": injury,
        "stats": [
            {"seasonId": 2026, "scoringPeriodId": 7, "statSourceId": 1, "appliedTotal": week_pts},
            {"seasonId": 2026, "scoringPeriodId": 7, "statSourceId": 0, "appliedTotal": 3.0},
            {"seasonId": 2026, "scoringPeriodId": 0, "statSourceId": 1, "appliedTotal": season_pts},
        ],
    }


def _entry(slot, player):
    return {"lineupSlotId": slot, "playerId": player["id"], "playerPoolEntry": {"player": player}}


ESPN_LEAGUE = {
    "scoringPeriodId": 7,
    "status": {"currentMatchupPeriod": 7},
    "settings": {
        "name": "ESPN Test",
        "rosterSettings": {"lineupSlotCounts": {"0": 1, "2": 1, "4": 1, "6": 1, "23": 1, "20": 3, "17": 0}},
        "scoringSettings": {"scoringItems": [{"statId": 53, "points": 0.5}]},
        "scheduleSettings": {"matchupPeriodCount": 14},
    },
    "members": [{"id": "{SWID-1}", "displayName": "espnme"}, {"id": "{SWID-2}", "displayName": "espnrival"}],
    "teams": [
        {"id": 1, "name": "Espn Mine", "primaryOwner": "{SWID-1}", "owners": ["{SWID-1}"],
         "record": {"overall": {"wins": 5, "losses": 1, "pointsFor": 800.5, "pointsAgainst": 600}},
         "roster": {"entries": [
             _entry(0, _espn_player(1, "Qb One", 1, 12, 20, 340)),
             _entry(2, _espn_player(2, "Rb One", 2, 25, 9, 150, injury="OUT")),
             _entry(4, _espn_player(3, "Wr One", 3, 15, 14, 240)),
             _entry(6, _espn_player(4, "Te One", 4, 33, 8, 130)),
             _entry(23, _espn_player(5, "Wr Two", 3, 8, 10, 170)),
             _entry(20, _espn_player(6, "Rb Two", 2, 6, 12, 200)),
         ]}},
        {"id": 2, "location": "Rival", "nickname": "Town", "primaryOwner": "{SWID-2}",
         "record": {"overall": {"wins": 1, "losses": 5, "pointsFor": 650, "pointsAgainst": 780}},
         "roster": {"entries": [_entry(0, _espn_player(11, "Qb Two", 1, 2, 17, 300))]}},
    ],
    "schedule": [
        {"matchupPeriodId": 6, "home": {"teamId": 1, "totalPoints": 100}, "away": {"teamId": 2, "totalPoints": 90}},
        {"matchupPeriodId": 7, "home": {"teamId": 2, "totalPoints": 0}, "away": {"teamId": 1, "totalPoints": 0}},
    ],
}

ESPN_FREE_AGENTS = {"players": [
    {"player": _espn_player(21, "Fa Back", 2, 3, 11, 190), "status": "FREEAGENT"},
    {"player": {**_espn_player(22, "Fa Kicker", 5, 3, 8, 130), "ownership": {"percentOwned": 12.345}},
     "status": "FREEAGENT"},
]}


class EspnFakeHttp(FakeHttp):
    def get(self, url, params=None, headers=None, cookies=None, cache_ttl=0):
        self.calls.append((url, params, headers))
        views = [v for k, v in params or [] if k == "view"]
        if "kona_player_info" in views:
            return ESPN_FREE_AGENTS
        if "mTransactions2" in views:
            return {"transactions": []}
        return ESPN_LEAGUE


@pytest.fixture
def espn_league():
    client = EspnClient("E1", 2026, espn_s2="s2", swid="{SWID-1}", http=EspnFakeHttp({}))
    return EspnLeague(client)
