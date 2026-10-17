"""The consultant: one object that answers fantasy questions across configured leagues.

Every public method returns plain JSON-serializable data, so the same calls back both the
CLI commands and the Claude agent's tools.
"""

from __future__ import annotations

import difflib
import re

from . import analysis
from .config import Config
from .espn import EspnClient, EspnLeague
from .http import JsonHttp
from .models import Player, Team
from .platforms import FantasyLeague
from .sleeper import SleeperClient, SleeperLeague


def _norm(name: str) -> str:
    name = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", name.lower())
    return re.sub(r"[^a-z0-9 ]", "", name).strip()


def find_player(query: str, players: list[Player]) -> Player:
    """Resolve a player name typed by a person (or a model) against a list of players."""
    q = _norm(query)
    exact = [p for p in players if _norm(p.name) == q or p.id == query]
    if exact:
        return exact[0]
    partial = [p for p in players if q and q in _norm(p.name)]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        raise KeyError(f"{query!r} is ambiguous: {', '.join(p.name for p in partial[:6])}")
    names = {_norm(p.name): p for p in players}
    close = difflib.get_close_matches(q, list(names), n=1, cutoff=0.8)
    if close:
        return names[close[0]]
    raise KeyError(f"No player matching {query!r}")


class Consultant:
    def __init__(self, config: Config, leagues: dict[str, FantasyLeague] | None = None,
                 sleeper: SleeperClient | None = None):
        self.config = config
        self.http = JsonHttp(cache_dir=config.cache_dir)
        self.sleeper = sleeper or SleeperClient(self.http)
        self.leagues: dict[str, FantasyLeague] = leagues if leagues is not None else self._configured_leagues()
        self.active_key: str | None = self._default_key()

    def _configured_leagues(self) -> dict[str, FantasyLeague]:
        c = self.config
        leagues: dict[str, FantasyLeague] = {}
        if c.sleeper_league_id:
            leagues[f"sleeper:{c.sleeper_league_id}"] = SleeperLeague(
                c.sleeper_league_id, self.sleeper, c.sleeper_username
            )
        if c.espn_league_id:
            client = EspnClient(c.espn_league_id, c.season, c.espn_s2, c.espn_swid, self.http)
            leagues[f"espn:{c.espn_league_id}"] = EspnLeague(client, c.espn_team_id)
        return leagues

    def _default_key(self) -> str | None:
        preferred = self.config.default_platform
        for key in self.leagues:
            if preferred is None or key.startswith(preferred + ":"):
                return key
        return next(iter(self.leagues), None)

    @property
    def active(self) -> FantasyLeague:
        if self.active_key is None:
            raise LookupError(
                "No league selected. Configure SLEEPER_LEAGUE_ID or ESPN_LEAGUE_ID, "
                "or call list_leagues/select_league."
            )
        return self.leagues[self.active_key]

    # League management ------------------------------------------------------

    def list_leagues(self) -> dict:
        """Configured leagues plus any Sleeper leagues found for SLEEPER_USERNAME."""
        configured = []
        for key, lg in self.leagues.items():
            info = lg.league()
            configured.append({"key": key, "name": info.name, "platform": info.platform,
                               "season": info.season, "active": key == self.active_key})
        discovered = []
        if self.config.sleeper_username:
            user = self.sleeper.get_user(self.config.sleeper_username)
            for lg in self.sleeper.get_user_leagues(user["user_id"], self.config.season):
                key = f"sleeper:{lg['league_id']}"
                if key not in self.leagues:
                    discovered.append({"key": key, "name": lg.get("name"), "platform": "sleeper",
                                       "season": lg.get("season")})
        return {"configured": configured, "discoverable_sleeper_leagues": discovered}

    def select_league(self, key: str) -> dict:
        """Switch the active league. Unconfigured Sleeper leagues ('sleeper:<id>') are added on the fly."""
        if key not in self.leagues:
            platform, _, league_id = key.partition(":")
            if platform == "sleeper" and league_id:
                self.leagues[key] = SleeperLeague(league_id, self.sleeper, self.config.sleeper_username)
            elif platform == "espn" and league_id:
                c = self.config
                client = EspnClient(league_id, c.season, c.espn_s2, c.espn_swid, self.http)
                self.leagues[key] = EspnLeague(client, None)
            else:
                raise KeyError(f"Unknown league key {key!r}; use 'sleeper:<id>' or 'espn:<id>'")
        self.active_key = key
        return self.league_overview()

    # Questions -----------------------------------------------------------------

    def league_overview(self) -> dict:
        lg = self.active
        info = lg.league()
        my_id = lg.my_team_id()
        return {
            "league": info.name,
            "platform": info.platform,
            "season": info.season,
            "current_week": info.current_week,
            "scoring": info.scoring,
            "teams": info.num_teams,
            "starting_slots": info.starting_slots,
            "my_team_id": my_id,
            "standings": analysis.standings(lg.teams()),
        }

    def _team_dict(self, team: Team) -> dict:
        return {
            **team.summary(),
            "roster": [
                {"slot": e.slot, **e.player.to_dict()}
                for e in sorted(team.roster, key=lambda e: (not e.is_starter, -e.player.projected))
            ],
        }

    def my_roster(self, week: int | None = None) -> dict:
        return self._team_dict(self.active.my_team(week))

    def team_roster(self, team: str, week: int | None = None) -> dict:
        return self._team_dict(self.active.find_team(team, week))

    def lineup(self, week: int | None = None) -> dict:
        lg = self.active
        return {"week": week or lg.league().current_week,
                **analysis.lineup_advice(lg.my_team(week), lg.league().roster_slots)}

    def _opponent(self, week: int | None) -> Team | None:
        lg = self.active
        my_id = lg.my_team_id()
        for m in lg.matchups(week):
            if m.home_team_id == my_id and m.away_team_id:
                return lg.team(m.away_team_id, week)
            if m.away_team_id == my_id:
                return lg.team(m.home_team_id, week)
        return None

    def matchup(self, week: int | None = None) -> dict:
        lg = self.active
        opponent = self._opponent(week)
        if opponent is None:
            return {"week": week or lg.league().current_week, "message": "No opponent this week (bye)."}
        preview = analysis.matchup_preview(lg.my_team(week), opponent, lg.league().roster_slots)
        return {"week": week or lg.league().current_week, **preview}

    def waivers(self, position: str | None = None, limit: int = 10) -> dict:
        lg = self.active
        pool = lg.free_agents(position, limit=300)
        if position is None:
            # Kickers and defenses are weekly streams; ask for them by position.
            pool = [p for p in pool if p.position not in ("K", "DEF")]
        return {
            "position_filter": position,
            "recommendations": analysis.waiver_targets(lg.my_team(), pool, lg.league().roster_slots, limit),
            "best_available": [p.to_dict() for p in pool[:limit]],
        }

    def trade(self, give: list[str], get: list[str], partner: str | None = None) -> dict:
        lg = self.active
        mine = lg.my_team()
        others = [t for t in lg.teams() if t.id != mine.id]
        partner_team = lg.find_team(partner) if partner else None
        give_players = [find_player(n, mine.players()) for n in give]
        search = partner_team.players() if partner_team else [p for t in others for p in t.players()]
        get_players = [find_player(n, search) for n in get]
        if partner_team is None and get_players:
            owner = next(t for t in others if any(p.id == get_players[0].id for p in t.players()))
            if all(any(p.id == g.id for p in owner.players()) for g in get_players):
                partner_team = owner
        return analysis.evaluate_trade(mine, give_players, get_players, lg.league().roster_slots, partner_team)

    def player_lookup(self, name: str) -> dict:
        """Find a player in the active league: who rosters them, or whether they are available."""
        lg = self.active
        rostered = [(t, e) for t in lg.teams() for e in t.roster]
        try:
            p = find_player(name, [e.player for _, e in rostered])
            t, e = next((t, e) for t, e in rostered if e.player.id == p.id)
            return {**p.to_dict(), "rostered_by": t.name, "slot": e.slot}
        except KeyError:
            pass
        try:
            p = find_player(name, lg.free_agents(limit=400))
            return {**p.to_dict(), "rostered_by": None, "available": True}
        except KeyError as exc:
            return {"error": str(exc)}

    def trending(self, kind: str = "add", limit: int = 15) -> dict:
        """Sleeper-wide trending adds/drops (a useful signal for any platform)."""
        lg = self.active
        if isinstance(lg, SleeperLeague):
            return {"kind": kind, "players": lg.trending(kind, limit)}
        players = self.sleeper.get_players()
        rostered = {_norm(p.name) for t in lg.teams() for p in t.players()}
        out = []
        for item in self.sleeper.get_trending(kind, limit=limit):
            info = players.get(str(item["player_id"]), {})
            name = info.get("full_name") or f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()
            out.append({"name": name, "position": info.get("position"), "team": info.get("team"),
                        "trend_count": item.get("count", 0), "rostered_in_league": _norm(name) in rostered})
        return {"kind": kind, "players": out}

    def transactions(self, limit: int = 20) -> dict:
        return {"transactions": self.active.recent_transactions(limit)}
