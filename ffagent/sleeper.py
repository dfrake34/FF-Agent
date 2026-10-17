"""Sleeper API client (https://docs.sleeper.com) and league adapter.

The Sleeper API is public and read-only; no credentials are needed. Sleeper asks
callers to fetch the full player dump at most once a day, so it is cached on disk.
Projections come from api.sleeper.com, the undocumented endpoint the Sleeper app uses.
"""

from __future__ import annotations

from functools import cached_property

from .http import JsonHttp
from .models import League, Matchup, Player, RosterEntry, Team
from .platforms import FantasyLeague

BASE_URL = "https://api.sleeper.app/v1"
PROJECTIONS_URL = "https://api.sleeper.com/projections/nfl"
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
DAY = 24 * 60 * 60


class SleeperClient:
    def __init__(self, http: JsonHttp | None = None):
        self.http = http or JsonHttp()

    def _get(self, path: str, params=None, cache_ttl: float = 0):
        return self.http.get(f"{BASE_URL}{path}", params=params, cache_ttl=cache_ttl)

    # Users & leagues
    def get_user(self, username_or_id: str) -> dict:
        user = self._get(f"/user/{username_or_id}")
        if not user:
            raise KeyError(f"Sleeper user {username_or_id!r} not found")
        return user

    def get_user_leagues(self, user_id: str, season: int) -> list[dict]:
        return self._get(f"/user/{user_id}/leagues/nfl/{season}") or []

    def get_league(self, league_id: str) -> dict:
        return self._get(f"/league/{league_id}")

    def get_rosters(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/rosters") or []

    def get_league_users(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/users") or []

    def get_matchups(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/matchups/{week}") or []

    def get_transactions(self, league_id: str, week: int) -> list[dict]:
        return self._get(f"/league/{league_id}/transactions/{week}") or []

    def get_traded_picks(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/traded_picks") or []

    def get_league_drafts(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/drafts") or []

    def get_draft_picks(self, draft_id: str) -> list[dict]:
        return self._get(f"/draft/{draft_id}/picks") or []

    # NFL-wide data
    def get_state(self) -> dict:
        return self._get("/state/nfl", cache_ttl=60 * 60)

    def get_players(self) -> dict[str, dict]:
        return self._get("/players/nfl", cache_ttl=DAY)

    def get_trending(self, kind: str = "add", lookback_hours: int = 24, limit: int = 25) -> list[dict]:
        return self._get(
            f"/players/nfl/trending/{kind}",
            params={"lookback_hours": lookback_hours, "limit": limit},
            cache_ttl=15 * 60,
        )

    def get_projections(self, season: int, week: int | None = None) -> list[dict]:
        """Weekly projections, or full-season projections when `week` is None."""
        url = f"{PROJECTIONS_URL}/{season}" + (f"/{week}" if week else "")
        params = [("season_type", "regular")] + [("position[]", p) for p in FANTASY_POSITIONS]
        return self.http.get(url, params=params, cache_ttl=60 * 60) or []


def score_stats(stats: dict, scoring_settings: dict) -> float:
    """Apply a Sleeper league's scoring settings to a stat line."""
    if scoring_settings:
        total = sum(float(stats.get(k, 0) or 0) * float(v) for k, v in scoring_settings.items())
        if total:
            return round(total, 2)
    rec = float((scoring_settings or {}).get("rec", 1))
    key = "pts_ppr" if rec >= 1 else "pts_half_ppr" if rec >= 0.5 else "pts_std"
    return round(float(stats.get(key, 0) or 0), 2)


def describe_scoring(scoring_settings: dict) -> str:
    rec = float(scoring_settings.get("rec", 0))
    label = "PPR" if rec >= 1 else "Half PPR" if rec >= 0.5 else "Standard"
    if float(scoring_settings.get("bonus_rec_te", 0)) > 0:
        label += " + TE premium"
    if float(scoring_settings.get("pass_td", 4)) >= 6:
        label += ", 6pt pass TD"
    return label


class SleeperLeague(FantasyLeague):
    platform = "sleeper"

    def __init__(
        self,
        league_id: str,
        client: SleeperClient | None = None,
        username: str | None = None,
    ):
        self.league_id = str(league_id)
        self.client = client or SleeperClient()
        self.username = username
        self._teams_cache: dict[int, list[Team]] = {}
        self._proj_cache: dict[int | None, dict[str, float]] = {}

    # Raw data ---------------------------------------------------------------

    @cached_property
    def _league(self) -> dict:
        return self.client.get_league(self.league_id)

    @cached_property
    def _state(self) -> dict:
        return self.client.get_state()

    @cached_property
    def _players(self) -> dict[str, dict]:
        return self.client.get_players()

    @cached_property
    def _users(self) -> dict[str, dict]:
        return {u["user_id"]: u for u in self.client.get_league_users(self.league_id)}

    @cached_property
    def _user_id(self) -> str | None:
        if not self.username:
            return None
        return self.client.get_user(self.username)["user_id"]

    @property
    def season(self) -> int:
        return int(self._league.get("season") or self._state.get("season"))

    @property
    def current_week(self) -> int:
        if self._state.get("season_type") != "regular":
            return 1
        return max(1, int(self._state.get("display_week") or self._state.get("week") or 1))

    def projections(self, week: int | None) -> dict[str, float]:
        """player_id -> projected fantasy points under this league's scoring (week=None: season)."""
        if week not in self._proj_cache:
            scoring = self._league.get("scoring_settings") or {}
            self._proj_cache[week] = {
                str(p["player_id"]): score_stats(p.get("stats") or {}, scoring)
                for p in self.client.get_projections(self.season, week)
            }
        return self._proj_cache[week]

    def _player(self, player_id: str, week: int) -> Player:
        info = self._players.get(player_id, {})
        name = info.get("full_name") or " ".join(
            x for x in (info.get("first_name"), info.get("last_name")) if x
        )
        position = info.get("position") or (info.get("fantasy_positions") or ["?"])[0]
        remaining = max(1, self.league().total_weeks - self.current_week + 1)
        season_proj = self.projections(None).get(player_id, 0.0)
        return Player(
            id=player_id,
            name=name or player_id,
            position=position,
            team=info.get("team"),
            injury_status=info.get("injury_status"),
            projected=self.projections(week).get(player_id, 0.0),
            ros_projected=round(season_proj * remaining / self.league().total_weeks, 1),
        )

    # FantasyLeague ----------------------------------------------------------

    def league(self) -> League:
        return self._league_model

    @cached_property
    def _league_model(self) -> League:
        lg = self._league
        settings = lg.get("settings") or {}
        playoff_start = int(settings.get("playoff_week_start") or 15)
        return League(
            platform=self.platform,
            id=self.league_id,
            name=lg.get("name", self.league_id),
            season=int(lg.get("season") or self._state.get("season")),
            current_week=self.current_week,
            roster_slots=list(lg.get("roster_positions") or []),
            scoring=describe_scoring(lg.get("scoring_settings") or {}),
            num_teams=int(lg.get("total_rosters") or len(self.client.get_rosters(self.league_id))),
            total_weeks=max(playoff_start + 2, 17),
        )

    def my_team_id(self) -> str | None:
        uid = self._user_id
        if uid is None:
            return None
        for r in self.client.get_rosters(self.league_id):
            if r.get("owner_id") == uid or uid in (r.get("co_owners") or []):
                return str(r["roster_id"])
        return None

    def teams(self, week: int | None = None) -> list[Team]:
        week = week or self.current_week
        if week in self._teams_cache:
            return self._teams_cache[week]
        starting_slots = self.league().starting_slots
        teams = []
        for r in self.client.get_rosters(self.league_id):
            owner = self._users.get(r.get("owner_id") or "", {})
            meta = owner.get("metadata") or {}
            s = r.get("settings") or {}
            starters = r.get("starters") or []
            reserve = set(r.get("reserve") or [])
            taxi = set(r.get("taxi") or [])
            entries = []
            for slot, pid in zip(starting_slots, starters):
                if pid and pid != "0":
                    entries.append(RosterEntry(self._player(pid, week), slot))
            placed = {e.player.id for e in entries}
            for pid in r.get("players") or []:
                if pid in placed:
                    continue
                slot = "IR" if pid in reserve else "TAXI" if pid in taxi else "BN"
                entries.append(RosterEntry(self._player(pid, week), slot))
            teams.append(
                Team(
                    id=str(r["roster_id"]),
                    name=meta.get("team_name") or owner.get("display_name") or f"Team {r['roster_id']}",
                    owner=owner.get("display_name") or "Unknown",
                    wins=int(s.get("wins", 0)),
                    losses=int(s.get("losses", 0)),
                    ties=int(s.get("ties", 0)),
                    points_for=float(s.get("fpts", 0)) + float(s.get("fpts_decimal", 0)) / 100,
                    points_against=float(s.get("fpts_against", 0))
                    + float(s.get("fpts_against_decimal", 0)) / 100,
                    roster=entries,
                )
            )
        self._teams_cache[week] = teams
        return teams

    def matchups(self, week: int | None = None) -> list[Matchup]:
        week = week or self.current_week
        by_id: dict[int, list[dict]] = {}
        result = []
        for m in self.client.get_matchups(self.league_id, week):
            if m.get("matchup_id") is None:
                result.append(Matchup(week, str(m["roster_id"]), None, float(m.get("points") or 0)))
            else:
                by_id.setdefault(m["matchup_id"], []).append(m)
        for pair in by_id.values():
            home = pair[0]
            away = pair[1] if len(pair) > 1 else None
            result.append(
                Matchup(
                    week=week,
                    home_team_id=str(home["roster_id"]),
                    away_team_id=str(away["roster_id"]) if away else None,
                    home_points=float(home.get("points") or 0),
                    away_points=float(away.get("points") or 0) if away else 0.0,
                )
            )
        return result

    def free_agents(
        self, position: str | None = None, limit: int = 25, week: int | None = None
    ) -> list[Player]:
        week = week or self.current_week
        rostered = {pid for r in self.client.get_rosters(self.league_id) for pid in r.get("players") or []}
        weekly = self.projections(week)
        season = self.projections(None)
        candidates = []
        for pid, info in self._players.items():
            if pid in rostered or not info.get("active", pid.isalpha()):
                continue
            pos = info.get("position")
            if pos not in FANTASY_POSITIONS or (position and pos != position.upper()):
                continue
            if weekly.get(pid, 0) <= 0 and season.get(pid, 0) <= 0:
                continue
            candidates.append(pid)
        players = [self._player(pid, week) for pid in candidates]
        players.sort(key=lambda p: (p.projected, p.ros_projected), reverse=True)
        return players[:limit]

    def recent_transactions(self, limit: int = 20) -> list[dict]:
        names = {t.id: t.name for t in self.teams()}
        out = []
        for week in range(self.current_week, max(0, self.current_week - 3), -1):
            for tx in self.client.get_transactions(self.league_id, week):
                if tx.get("status") != "complete":
                    continue
                out.append(
                    {
                        "week": week,
                        "type": tx.get("type"),
                        "teams": [names.get(str(rid), str(rid)) for rid in tx.get("roster_ids") or []],
                        "adds": {
                            self._player(pid, week).name: names.get(str(rid), str(rid))
                            for pid, rid in (tx.get("adds") or {}).items()
                        },
                        "drops": {
                            self._player(pid, week).name: names.get(str(rid), str(rid))
                            for pid, rid in (tx.get("drops") or {}).items()
                        },
                        "created": tx.get("created"),
                    }
                )
        out.sort(key=lambda t: t.get("created") or 0, reverse=True)
        return out[:limit]

    def trending(self, kind: str = "add", limit: int = 15) -> list[dict]:
        rostered = {pid for r in self.client.get_rosters(self.league_id) for pid in r.get("players") or []}
        week = self.current_week
        out = []
        for item in self.client.get_trending(kind, limit=limit):
            p = self._player(str(item["player_id"]), week)
            out.append({**p.to_dict(), "trend_count": item.get("count", 0), "rostered_in_league": p.id in rostered})
        return out
