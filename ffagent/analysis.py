"""Platform-independent fantasy analysis: lineups, waivers, trades, matchups, standings."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .models import SLOT_ELIGIBILITY, Player, Team

# Players with these statuses are not expected to play this week.
INACTIVE_STATUSES = {"OUT", "IR", "PUP", "SUS", "SUSPENSION", "INJURY_RESERVE", "NA", "DNR", "COV"}


def is_available(p: Player) -> bool:
    return (p.injury_status or "").upper() not in INACTIVE_STATUSES


def weekly_value(p: Player) -> float:
    return p.projected if is_available(p) else 0.0


def ros_value(p: Player) -> float:
    return p.ros_projected


@dataclass
class Lineup:
    starters: list[tuple[str, Player]] = field(default_factory=list)
    bench: list[Player] = field(default_factory=list)
    empty_slots: list[str] = field(default_factory=list)
    total: float = 0.0

    def to_dict(self) -> dict:
        return {
            "starters": [
                {"slot": s, "player": p.name, "position": p.position, "team": p.team,
                 "opponent": p.opponent, "projected": p.projected, "injury_status": p.injury_status}
                for s, p in self.starters
            ],
            "bench": [{"player": p.name, "position": p.position, "opponent": p.opponent,
                       "projected": p.projected, "injury_status": p.injury_status} for p in self.bench],
            "empty_slots": self.empty_slots,
            "projected_total": round(self.total, 2),
        }


def optimize_lineup(players: list[Player], slots: list[str], value=weekly_value) -> Lineup:
    """Pick the highest-value starting lineup for the given starting slots.

    Fills the most restrictive slots first (QB, RB, ... then FLEX, then SUPER_FLEX), taking
    the best eligible player each time. Because standard flex slots are supersets of the
    dedicated slots, this greedy order yields the optimal lineup for normal league formats.
    """
    starting = [s for s in slots if s in SLOT_ELIGIBILITY]
    order = sorted(range(len(starting)), key=lambda i: len(SLOT_ELIGIBILITY[starting[i]]))
    pool = sorted(players, key=value, reverse=True)
    used: set[str] = set()
    chosen: dict[int, Player] = {}
    lineup = Lineup()
    for i in order:
        eligible = SLOT_ELIGIBILITY[starting[i]]
        pick = next((p for p in pool if p.id not in used and p.position in eligible), None)
        if pick is None:
            lineup.empty_slots.append(starting[i])
            continue
        used.add(pick.id)
        chosen[i] = pick
    lineup.starters = [(starting[i], chosen[i]) for i in range(len(starting)) if i in chosen]
    lineup.bench = [p for p in pool if p.id not in used]
    lineup.total = sum(value(p) for _, p in lineup.starters)
    return lineup


def lineup_advice(team: Team, slots: list[str]) -> dict:
    """Compare the team's current starters with the optimal lineup for the week."""
    optimal = optimize_lineup(team.players(), slots)
    current = [e.player for e in team.roster if e.is_starter]
    current_total = sum(weekly_value(p) for p in current)
    optimal_ids = {p.id for _, p in optimal.starters}
    current_ids = {p.id for p in current}
    return {
        "team": team.name,
        "optimal_lineup": optimal.to_dict(),
        "current_projected_total": round(current_total, 2),
        "projected_gain": round(optimal.total - current_total, 2),
        "start": [p.name for _, p in optimal.starters if p.id not in current_ids],
        "bench": [p.name for p in current if p.id not in optimal_ids],
        "injured_starters": [p.name for p in current if not is_available(p)],
    }


def waiver_targets(team: Team, free_agents: list[Player], slots: list[str], limit: int = 10) -> list[dict]:
    """Rank free agents by how much they would improve the team's starting lineup.

    For each free agent, the gain is the change in optimal lineup value from adding them, for
    this week and for the rest of the season. The suggested drop is the lowest rest-of-season
    value player who would not start in the improved rest-of-season lineup; players in IR or
    taxi slots are never suggested, since dropping them doesn't open a roster spot.
    """
    roster = team.players()
    stashed = {e.player.id for e in team.roster if e.slot in ("IR", "TAXI")}
    base_week = optimize_lineup(roster, slots).total
    base_ros = optimize_lineup(roster, slots, ros_value).total
    out = []
    for fa in free_agents:
        week_gain = optimize_lineup(roster + [fa], slots).total - base_week
        ros_lineup = optimize_lineup(roster + [fa], slots, ros_value)
        ros_gain = ros_lineup.total - base_ros
        if week_gain <= 0 and ros_gain <= 0:
            continue
        droppable = [p for p in ros_lineup.bench if p.id != fa.id and p.id not in stashed]
        drop = min(droppable, key=lambda p: (p.ros_projected, p.projected)) if droppable else None
        out.append(
            {
                "player": fa.name,
                "position": fa.position,
                "team": fa.team,
                "opponent": fa.opponent,
                "injury_status": fa.injury_status,
                "projected_this_week": fa.projected,
                "ros_projected": fa.ros_projected,
                "percent_owned": fa.percent_owned,
                "weekly_lineup_gain": round(week_gain, 2),
                "ros_lineup_gain": round(ros_gain, 1),
                "suggested_drop": drop.name if drop else None,
            }
        )
    out.sort(key=lambda r: (r["ros_lineup_gain"], r["weekly_lineup_gain"]), reverse=True)
    return out[:limit]


def _swap(roster: list[Player], give: list[Player], get: list[Player]) -> list[Player]:
    give_ids = {p.id for p in give}
    return [p for p in roster if p.id not in give_ids] + get


def evaluate_trade(
    my_team: Team, give: list[Player], get: list[Player], slots: list[str], partner: Team | None = None
) -> dict:
    """Judge a trade by its effect on each side's rest-of-season starting lineup."""
    mine_before = optimize_lineup(my_team.players(), slots, ros_value).total
    mine_after = optimize_lineup(_swap(my_team.players(), give, get), slots, ros_value).total
    delta = mine_after - mine_before
    result = {
        "give": [{"player": p.name, "position": p.position, "ros_projected": p.ros_projected} for p in give],
        "get": [{"player": p.name, "position": p.position, "ros_projected": p.ros_projected} for p in get],
        "raw_ros_value_given": round(sum(ros_value(p) for p in give), 1),
        "raw_ros_value_received": round(sum(ros_value(p) for p in get), 1),
        "my_ros_lineup_before": round(mine_before, 1),
        "my_ros_lineup_after": round(mine_after, 1),
        "my_ros_lineup_change": round(delta, 1),
        "roster_spots_change": len(get) - len(give),
    }
    if partner is not None:
        p_before = optimize_lineup(partner.players(), slots, ros_value).total
        p_after = optimize_lineup(_swap(partner.players(), get, give), slots, ros_value).total
        result["partner"] = partner.name
        result["partner_ros_lineup_change"] = round(p_after - p_before, 1)
    pct = delta / mine_before * 100 if mine_before else 0.0
    result["verdict"] = (
        "accept" if pct >= 2 else "slight edge" if pct > 0 else "decline" if pct <= -2 else "slightly negative"
    )
    return result


def _player_sd(points: float) -> float:
    # Weekly fantasy scores are noisy: roughly 45% of projection, with a floor.
    return max(4.0, 0.45 * points)


def win_probability(my_players: list[Player], opp_players: list[Player]) -> float:
    my_total = sum(weekly_value(p) for p in my_players)
    opp_total = sum(weekly_value(p) for p in opp_players)
    var = sum(_player_sd(weekly_value(p)) ** 2 for p in my_players + opp_players)
    if var == 0:
        return 0.5
    z = (my_total - opp_total) / math.sqrt(var)
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def matchup_preview(my_team: Team, opponent: Team, slots: list[str]) -> dict:
    """Project this week's matchup using my optimal lineup against the opponent's set lineup."""
    mine = optimize_lineup(my_team.players(), slots)
    opp_starters = [e.player for e in opponent.roster if e.is_starter]
    if not opp_starters:
        opp_starters = [p for _, p in optimize_lineup(opponent.players(), slots).starters]
    opp_total = sum(weekly_value(p) for p in opp_starters)
    my_starters = [p for _, p in mine.starters]
    return {
        "my_team": my_team.name,
        "opponent": opponent.name,
        "my_projected": round(mine.total, 2),
        "opponent_projected": round(opp_total, 2),
        "win_probability": round(win_probability(my_starters, opp_starters), 3),
        "my_lineup": mine.to_dict()["starters"],
        "opponent_lineup": [
            {"player": p.name, "position": p.position, "opponent": p.opponent, "projected": p.projected,
             "injury_status": p.injury_status}
            for p in opp_starters
        ],
    }


def standings(teams: list[Team]) -> list[dict]:
    """Standings with a luck indicator: actual wins minus wins expected from points scored."""
    n = len(teams)
    by_pf = sorted(teams, key=lambda t: t.points_for, reverse=True)
    pf_rank = {t.id: i for i, t in enumerate(by_pf)}
    rows = []
    for t in teams:
        games = t.wins + t.losses + t.ties
        expected_pct = (n - 1 - pf_rank[t.id]) / (n - 1) if n > 1 else 0.5
        rows.append(
            {
                **t.summary(),
                "win_pct": round((t.wins + 0.5 * t.ties) / games, 3) if games else 0.0,
                "points_per_game": round(t.points_for / games, 2) if games else 0.0,
                "points_for_rank": pf_rank[t.id] + 1,
                "luck": round(t.wins + 0.5 * t.ties - expected_pct * games, 1),
            }
        )
    rows.sort(key=lambda r: (r["win_pct"], r["points_for"]), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows
