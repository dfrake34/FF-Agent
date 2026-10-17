"""Common interface implemented by every fantasy platform adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import League, Matchup, Player, Team


class FantasyLeague(ABC):
    """One league on one platform, seen from the perspective of the user's team."""

    platform: str

    @abstractmethod
    def league(self) -> League: ...

    @abstractmethod
    def teams(self, week: int | None = None) -> list[Team]:
        """All teams with rosters; players carry projections for `week` (default: current)."""

    @abstractmethod
    def my_team_id(self) -> str | None: ...

    @abstractmethod
    def matchups(self, week: int | None = None) -> list[Matchup]: ...

    @abstractmethod
    def free_agents(
        self, position: str | None = None, limit: int = 25, week: int | None = None
    ) -> list[Player]:
        """Best available unrostered players, sorted by projected points for `week`."""

    @abstractmethod
    def recent_transactions(self, limit: int = 20) -> list[dict]: ...

    # Helpers shared by all adapters -----------------------------------------

    def team(self, team_id: str, week: int | None = None) -> Team:
        for t in self.teams(week):
            if t.id == str(team_id):
                return t
        raise KeyError(f"No team with id {team_id}")

    def my_team(self, week: int | None = None) -> Team:
        team_id = self.my_team_id()
        if team_id is None:
            raise LookupError(
                "Could not tell which team is yours; set SLEEPER_USERNAME or ESPN_TEAM_ID / ESPN_SWID"
            )
        return self.team(team_id, week)

    def find_team(self, query: str, week: int | None = None) -> Team:
        """Find a team by id, team name or owner name (case-insensitive, substring match)."""
        q = query.strip().lower()
        teams = self.teams(week)
        for t in teams:
            if t.id == query or t.name.lower() == q or t.owner.lower() == q:
                return t
        matches = [t for t in teams if q in t.name.lower() or q in t.owner.lower()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise KeyError(f"No team matches {query!r}")
        raise KeyError(f"{query!r} matches several teams: {', '.join(t.name for t in matches)}")
