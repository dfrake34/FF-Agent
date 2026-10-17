"""Command-line interface: `ffagent chat` for the agent, plus direct analysis commands."""

from __future__ import annotations

import argparse
import json
import sys

from .config import Config
from .consultant import Consultant
from .http import ApiError


def _table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "(none)"
    cells = [[str(r.get(c, "") if r.get(c) is not None else "") for c in columns] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(columns)]
    line = lambda vals: "  ".join(v.ljust(w) for v, w in zip(vals, widths))  # noqa: E731
    return "\n".join([line(columns), line(["-" * w for w in widths])] + [line(r) for r in cells])


def _print_text(command: str, data: dict) -> None:
    if command == "leagues":
        print(_table(data["configured"], ["key", "name", "platform", "season", "active"]))
        if data["discoverable_sleeper_leagues"]:
            print("\nOther Sleeper leagues (use --league KEY):")
            print(_table(data["discoverable_sleeper_leagues"], ["key", "name", "season"]))
    elif command == "standings":
        print(f"{data['league']} ({data['platform']}, {data['scoring']}) - week {data['current_week']}\n")
        print(_table(data["standings"], ["rank", "name", "owner", "record", "points_for", "points_per_game", "luck"]))
    elif command in ("roster",):
        print(f"{data['name']} ({data['record']})\n")
        print(_table(data["roster"], ["slot", "name", "position", "team", "opponent", "projected", "ros_projected", "injury_status"]))
    elif command == "lineup":
        opt = data["optimal_lineup"]
        print(f"Week {data['week']} optimal lineup for {data['team']}: {opt['projected_total']} pts "
              f"(current {data['current_projected_total']}, gain {data['projected_gain']:+})\n")
        print(_table(opt["starters"], ["slot", "player", "position", "team", "opponent", "projected", "injury_status"]))
        if data["start"] or data["bench"]:
            print(f"\nStart: {', '.join(data['start']) or '-'}\nBench: {', '.join(data['bench']) or '-'}")
        if data["injured_starters"]:
            print(f"Injured starters: {', '.join(data['injured_starters'])}")
    elif command == "matchup":
        if "message" in data:
            print(data["message"])
            return
        print(f"Week {data['week']}: {data['my_team']} {data['my_projected']} vs "
              f"{data['opponent']} {data['opponent_projected']} - win probability "
              f"{data['win_probability']:.0%}\n")
        print(_table(data["my_lineup"], ["slot", "player", "position", "opponent", "projected"]))
        print()
        print(_table(data["opponent_lineup"], ["player", "position", "opponent", "projected", "injury_status"]))
    elif command == "waivers":
        if not data["recommendations"]:
            print("No free agent improves your starting lineup. Best available:\n")
            print(_table(data["best_available"], ["name", "position", "team", "opponent", "projected",
                                                  "ros_projected", "injury_status"]))
            return
        print(_table(data["recommendations"], ["player", "position", "team", "opponent", "projected_this_week",
                                               "ros_projected", "weekly_lineup_gain", "ros_lineup_gain",
                                               "suggested_drop"]))
    elif command == "trade":
        print(f"Verdict: {data['verdict']}")
        print(f"Your ROS starting lineup: {data['my_ros_lineup_before']} -> {data['my_ros_lineup_after']} "
              f"({data['my_ros_lineup_change']:+})")
        if "partner" in data:
            print(f"{data['partner']}'s ROS lineup change: {data['partner_ros_lineup_change']:+}")
        if data["roster_spots_change"] > 0:
            print(f"You will need to drop {data['roster_spots_change']} player(s).")
    elif command == "trending":
        print(_table(data["players"], ["name", "position", "team", "trend_count", "rostered_in_league"]))
    else:
        print(json.dumps(data, indent=2, default=str))


def _chat(consultant: Consultant, question: str | None, effort: str, web_search: bool) -> None:
    from .agent import FantasyAgent

    agent = FantasyAgent(consultant, effort=effort, web_search=web_search)
    show_tool = lambda name, args: print(f"  [{name} {json.dumps(args) if args else ''}]", file=sys.stderr)  # noqa: E731
    if question:
        print(agent.ask(question, on_tool=show_tool))
        return
    print("Fantasy football consultant. Ask anything; 'reset' clears the conversation, 'quit' exits.")
    while True:
        try:
            q = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if q.lower() in ("quit", "exit", "q"):
            return
        if q.lower() == "reset":
            agent.reset()
            continue
        if q:
            print("\n" + agent.ask(q, on_tool=show_tool))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ffagent", description="Fantasy football consultant for Sleeper and ESPN.")
    p.add_argument("--league", help="League key, e.g. sleeper:1234 or espn:5678 (default: first configured)")
    p.add_argument("--json", action="store_true", help="Print raw JSON")
    sub = p.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="Talk to the Claude-powered agent (interactive, or one question)")
    chat.add_argument("question", nargs="?", help="Ask a single question and exit")
    chat.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    chat.add_argument("--no-web", action="store_true", help="Disable web search for news")

    sub.add_parser("leagues", help="List configured and discoverable leagues")
    sub.add_parser("standings", help="League overview and standings")
    for name, helptext in (("roster", "Your roster"), ("lineup", "Optimal lineup vs. current"),
                           ("matchup", "Matchup preview and win probability")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("--week", type=int)
    w = sub.add_parser("waivers", help="Best waiver pickups for your team")
    w.add_argument("--position", choices=["QB", "RB", "WR", "TE", "K", "DEF"])
    w.add_argument("--limit", type=int, default=10)
    t = sub.add_parser("trade", help="Evaluate a trade")
    t.add_argument("--give", nargs="+", required=True, metavar="PLAYER")
    t.add_argument("--get", nargs="+", required=True, metavar="PLAYER")
    t.add_argument("--partner", help="Trade partner team or owner name")
    tr = sub.add_parser("trending", help="Trending adds/drops on Sleeper")
    tr.add_argument("--kind", choices=["add", "drop"], default="add")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    consultant = Consultant(Config.from_env())
    try:
        if args.league:
            consultant.select_league(args.league)
        if args.command == "chat":
            _chat(consultant, args.question, args.effort, not args.no_web)
            return 0
        week = getattr(args, "week", None)
        data = {
            "leagues": consultant.list_leagues,
            "standings": consultant.league_overview,
            "roster": lambda: consultant.my_roster(week),
            "lineup": lambda: consultant.lineup(week),
            "matchup": lambda: consultant.matchup(week),
            "waivers": lambda: consultant.waivers(args.position, args.limit),
            "trade": lambda: consultant.trade(args.give, args.get, args.partner),
            "trending": lambda: consultant.trending(args.kind),
        }[args.command]()
    except (ApiError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        _print_text(args.command, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
