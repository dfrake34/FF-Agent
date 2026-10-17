"""ESPN Fantasy Football API client and league adapter.

ESPN's fantasy API is undocumented. Public leagues can be read anonymously; private
leagues need the `espn_s2` and `SWID` cookies from a logged-in browser session
(DevTools -> Application -> Cookies -> espn.com).
"""

from __future__ import annotations

import json
from functools import cached_property

from .http import JsonHttp
from .models import League, Matchup, Player, RosterEntry, Team
from .platforms import FantasyLeague

BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}"

# ESPN numeric ids -> normalized names
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}
LINEUP_SLOTS = {
    0: "QB", 2: "RB", 3: "WRRB_FLEX", 4: "WR", 5: "REC_FLEX", 6: "TE", 7: "SUPER_FLEX",
    16: "DEF", 17: "K", 20: "BN", 21: "IR", 23: "FLEX",
}
SLOT_IDS = {name: sid for sid, name in LINEUP_SLOTS.items()}
PRO_TEAMS = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET",
    9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN",
    17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC",
    25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}
# ESPN slot display order, so lineups read QB, RB, WR, TE, FLEX..., K, DEF, BN
SLOT_ORDER = [0, 2, 4, 6, 3, 5, 23, 7, 17, 16, 20, 21]

# Stat sources
ACTUAL, PROJECTED = 0, 1


class EspnClient:
    def __init__(
        self,
        league_id: str,
        season: int,
        espn_s2: str | None = None,
        swid: str | None = None,
        http: JsonHttp | None = None,
    ):
        self.league_id = str(league_id)
        self.season = int(season)
        self.http = http or JsonHttp()
        self.cookies = {"espn_s2": espn_s2, "SWID": swid} if espn_s2 and swid else None

    @property
    def url(self) -> str:
        return BASE_URL.format(season=self.season, league_id=self.league_id)

    def get(self, views: list[str], scoring_period: int | None = None, fantasy_filter: dict | None = None,
            extra_path: str = "") -> dict:
        params: list[tuple[str, str | int]] = [("view", v) for v in views]
        if scoring_period:
            params.append(("scoringPeriodId", scoring_period))
        headers = {"x-fantasy-filter": json.dumps(fantasy_filter)} if fantasy_filter else None
        return self.http.get(self.url + extra_path, params=params, headers=headers, cookies=self.cookies)

    def get_league(self, scoring_period: int | None = None) -> dict:
        return self.get(["mSettings", "mStatus", "mTeam", "mRoster", "mMatchupScore"], scoring_period)

    def get_schedule(self) -> dict:
        return self.get(["mMatchupScore", "mStatus"])

    def get_players(self, scoring_period: int, statuses=("FREEAGENT", "WAIVERS"),
                    slot_ids: list[int] | None = None, limit: int = 50) -> list[dict]:
        """Players from the player pool, ordered by ownership, with weekly + season projections."""
        players_filter: dict = {
            "filterStatus": {"value": list(statuses)},
            "limit": limit,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
            "filterRanksForScoringPeriodIds": {"value": [scoring_period]},
            "filterStatsForTopScoringPeriodIds": {
                "value": 2,
                "additionalValue": [f"00{self.season}", f"10{self.season}", f"11{self.season}{scoring_period}"],
            },
        }
        if slot_ids:
            players_filter["filterSlotIds"] = {"value": slot_ids}
        data = self.get(["kona_player_info"], scoring_period, {"players": players_filter})
        return data.get("players", [])

    def get_transactions(self, scoring_period: int) -> dict:
        return self.get(["mTransactions2"], scoring_period)


def _projection(stats: list[dict], season: int, scoring_period: int) -> float:
    for s in stats or []:
        if (s.get("statSourceId") == PROJECTED and s.get("scoringPeriodId") == scoring_period
                and s.get("seasonId", season) == season):
            return round(float(s.get("appliedTotal") or 0), 2)
    return 0.0


def parse_player(raw: dict, season: int, week: int, current_week: int, total_weeks: int) -> Player:
    """Build a Player from an ESPN `player` object (as found under playerPoolEntry or kona)."""
    stats = raw.get("stats") or []
    season_proj = _projection(stats, season, 0)
    remaining = max(1, total_weeks - current_week + 1)
    owned = (raw.get("ownership") or {}).get("percentOwned")
    return Player(
        id=str(raw.get("id")),
        name=raw.get("fullName", str(raw.get("id"))),
        position=POSITIONS.get(raw.get("defaultPositionId"), "?"),
        team=PRO_TEAMS.get(raw.get("proTeamId")),
        injury_status=(raw.get("injuryStatus") if raw.get("injuryStatus") not in (None, "ACTIVE", "NORMAL") else None),
        projected=_projection(stats, season, week),
        ros_projected=round(season_proj * remaining / total_weeks, 1),
        percent_owned=round(owned, 1) if owned is not None else None,
    )


def describe_scoring(settings: dict) -> str:
    # statId 53 = receptions
    for item in (settings.get("scoringSettings") or {}).get("scoringItems", []):
        if item.get("statId") == 53:
            pts = float(item.get("points", 0))
            return "PPR" if pts >= 1 else "Half PPR" if pts >= 0.5 else "Standard"
    return "Standard"


class EspnLeague(FantasyLeague):
    platform = "espn"

    def __init__(self, client: EspnClient, team_id: str | None = None):
        self.client = client
        self._team_id = str(team_id) if team_id else None
        self._data_cache: dict[int, dict] = {}

    def _data(self, week: int | None = None) -> dict:
        week = week or self.current_week
        if week not in self._data_cache:
            self._data_cache[week] = self.client.get_league(week)
        return self._data_cache[week]

    @cached_property
    def _base(self) -> dict:
        return self.client.get_league()

    @property
    def current_week(self) -> int:
        return int(self._base.get("scoringPeriodId") or (self._base.get("status") or {}).get("currentMatchupPeriod") or 1)

    @cached_property
    def _league_model(self) -> League:
        data = self._base
        settings = data.get("settings") or {}
        counts = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}
        slots: list[str] = []
        for sid in SLOT_ORDER:
            slots += [LINEUP_SLOTS[sid]] * int(counts.get(str(sid), 0))
        schedule = settings.get("scheduleSettings") or {}
        return League(
            platform=self.platform,
            id=self.client.league_id,
            name=settings.get("name", self.client.league_id),
            season=self.client.season,
            current_week=self.current_week,
            roster_slots=slots,
            scoring=describe_scoring(settings),
            num_teams=len(data.get("teams") or []),
            total_weeks=int(schedule.get("matchupPeriodCount", 14)) + 3,
        )

    def league(self) -> League:
        return self._league_model

    def my_team_id(self) -> str | None:
        if self._team_id:
            return self._team_id
        swid = (self.client.cookies or {}).get("SWID")
        if swid:
            for t in self._base.get("teams") or []:
                if swid in (t.get("owners") or []) or swid == t.get("primaryOwner"):
                    return str(t["id"])
        return None

    def _player(self, raw: dict, week: int) -> Player:
        lg = self.league()
        return parse_player(raw, self.client.season, week, self.current_week, lg.total_weeks)

    def teams(self, week: int | None = None) -> list[Team]:
        week = week or self.current_week
        data = self._data(week)
        members = {m["id"]: m for m in data.get("members") or []}
        teams = []
        for t in data.get("teams") or []:
            owner_id = t.get("primaryOwner") or (t.get("owners") or [None])[0]
            m = members.get(owner_id, {})
            owner = m.get("displayName") or " ".join(x for x in (m.get("firstName"), m.get("lastName")) if x)
            overall = (t.get("record") or {}).get("overall") or {}
            entries = []
            for e in (t.get("roster") or {}).get("entries") or []:
                raw = (e.get("playerPoolEntry") or {}).get("player") or {}
                slot = LINEUP_SLOTS.get(e.get("lineupSlotId"), "BN")
                entries.append(RosterEntry(self._player(raw, week), slot))
            name = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip()
            teams.append(
                Team(
                    id=str(t["id"]),
                    name=name or t.get("abbrev") or f"Team {t['id']}",
                    owner=owner or "Unknown",
                    wins=int(overall.get("wins", 0)),
                    losses=int(overall.get("losses", 0)),
                    ties=int(overall.get("ties", 0)),
                    points_for=float(overall.get("pointsFor", 0)),
                    points_against=float(overall.get("pointsAgainst", 0)),
                    roster=entries,
                )
            )
        return teams

    def matchups(self, week: int | None = None) -> list[Matchup]:
        week = week or (self._base.get("status") or {}).get("currentMatchupPeriod") or self.current_week
        out = []
        for g in self._data().get("schedule") or []:
            if g.get("matchupPeriodId") != week:
                continue
            home, away = g.get("home") or {}, g.get("away")
            out.append(
                Matchup(
                    week=week,
                    home_team_id=str(home.get("teamId")),
                    away_team_id=str(away["teamId"]) if away else None,
                    home_points=float(home.get("totalPoints") or 0),
                    away_points=float(away.get("totalPoints") or 0) if away else 0.0,
                )
            )
        return out

    def free_agents(
        self, position: str | None = None, limit: int = 25, week: int | None = None
    ) -> list[Player]:
        week = week or self.current_week
        slot_ids = [SLOT_IDS[position.upper()]] if position and position.upper() in SLOT_IDS else None
        raw = self.client.get_players(week, slot_ids=slot_ids, limit=max(limit * 2, 50))
        players = [self._player(p.get("player") or {}, week) for p in raw]
        if position:
            players = [p for p in players if p.position == position.upper()]
        players.sort(key=lambda p: (p.projected, p.ros_projected), reverse=True)
        return players[:limit]

    def recent_transactions(self, limit: int = 20) -> list[dict]:
        names = {t.id: t.name for t in self.teams()}
        out = []
        for tx in self.client.get_transactions(self.current_week).get("transactions") or []:
            if tx.get("status") != "EXECUTED":
                continue
            out.append(
                {
                    "week": tx.get("scoringPeriodId"),
                    "type": tx.get("type"),
                    "team": names.get(str(tx.get("teamId")), str(tx.get("teamId"))),
                    "items": [
                        {"type": i.get("type"), "player_id": i.get("playerId"),
                         "from_team": names.get(str(i.get("fromTeamId"))),
                         "to_team": names.get(str(i.get("toTeamId")))}
                        for i in tx.get("items") or []
                    ],
                    "created": tx.get("proposedDate"),
                }
            )
        out.sort(key=lambda t: t.get("created") or 0, reverse=True)
        return out[:limit]
