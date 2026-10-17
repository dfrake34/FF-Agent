"""Platform-neutral data models shared by the Sleeper and ESPN adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Which player positions may fill each lineup slot. Slot names follow
# Sleeper's conventions; the ESPN adapter maps its numeric slot ids onto them.
SLOT_ELIGIBILITY: dict[str, frozenset[str]] = {
    "QB": frozenset({"QB"}),
    "RB": frozenset({"RB"}),
    "WR": frozenset({"WR"}),
    "TE": frozenset({"TE"}),
    "K": frozenset({"K"}),
    "DEF": frozenset({"DEF"}),
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"RB", "WR"}),
    "REC_FLEX": frozenset({"WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
}

# Slots that never score.
NON_STARTING_SLOTS = frozenset({"BN", "IR", "TAXI"})


@dataclass
class Player:
    id: str
    name: str
    position: str
    team: str | None = None
    injury_status: str | None = None
    projected: float = 0.0
    ros_projected: float = 0.0
    percent_owned: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RosterEntry:
    player: Player
    slot: str  # lineup slot the player currently occupies, e.g. "RB", "FLEX", "BN"

    @property
    def is_starter(self) -> bool:
        return self.slot not in NON_STARTING_SLOTS


@dataclass
class Team:
    id: str
    name: str
    owner: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    points_against: float = 0.0
    roster: list[RosterEntry] = field(default_factory=list)

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}" + (f"-{self.ties}" if self.ties else "")

    def players(self) -> list[Player]:
        return [e.player for e in self.roster]

    def summary(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "record": self.record,
            "points_for": round(self.points_for, 2),
            "points_against": round(self.points_against, 2),
        }


@dataclass
class Matchup:
    week: int
    home_team_id: str
    away_team_id: str | None  # None for a bye week
    home_points: float = 0.0
    away_points: float = 0.0


@dataclass
class League:
    platform: str  # "sleeper" or "espn"
    id: str
    name: str
    season: int
    current_week: int
    roster_slots: list[str]  # starting slots plus BN/IR, e.g. ["QB", "RB", "RB", ..., "BN"]
    scoring: str  # short human description, e.g. "PPR"
    num_teams: int
    total_weeks: int = 17

    @property
    def starting_slots(self) -> list[str]:
        return [s for s in self.roster_slots if s not in NON_STARTING_SLOTS]
