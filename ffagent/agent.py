"""Claude-powered fantasy football agent.

Claude answers questions by calling tools that wrap the Consultant (live Sleeper/ESPN
data plus the analysis engine), and can search the web for breaking news.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from typing import Any

import anthropic

from .consultant import Consultant

SYSTEM_PROMPT = """You are a sharp, practical fantasy football consultant. You manage the user's \
Sleeper and ESPN leagues with them: setting lineups, finding waiver pickups, judging trades, \
previewing matchups and sizing up the league.

How to work:
- Ground every recommendation in the tools. Pull live league data before giving advice, and \
use the league's own scoring and roster slots rather than generic rankings.
- Projections are a starting point, not the answer. Weigh injuries, bye weeks, role changes, \
matchups and recent news; use web search for injury reports and news that projections may \
not reflect yet, and say when you did.
- Be decisive. Give a clear call (start X, add Y, accept/decline) with the two or three \
reasons that matter most, and mention the closest alternative when a decision is close.
- Show the numbers that drive the call (projected points, lineup gain, win probability) but \
keep answers short and skimmable.
- If which team is the user's is unknown, or a name is ambiguous, say so and ask rather than \
guessing.
- You can read leagues but cannot make moves on the platforms; tell the user exactly what to \
do in the app."""

WEEK = {"type": "integer", "description": "NFL week number. Omit for the current week."}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_leagues",
        "description": "List the configured leagues (and other Sleeper leagues for the user) with their keys.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "select_league",
        "description": "Switch the active league by key, e.g. 'sleeper:1234567890' or 'espn:987654'. "
                       "Returns the new league's overview.",
        "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    },
    {
        "name": "league_overview",
        "description": "Active league settings (scoring, roster slots, current week) and standings, "
                       "including points-for rank and a luck indicator.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "my_roster",
        "description": "The user's roster with lineup slots, projections for the week, rest-of-season "
                       "projections and injury statuses.",
        "input_schema": {"type": "object", "properties": {"week": WEEK}},
    },
    {
        "name": "team_roster",
        "description": "Another team's roster, found by team name, owner name or team id.",
        "input_schema": {
            "type": "object",
            "properties": {"team": {"type": "string"}, "week": WEEK},
            "required": ["team"],
        },
    },
    {
        "name": "optimize_lineup",
        "description": "Optimal starting lineup for the user's team by projected points, compared with "
                       "the current lineup: who to start, who to bench, injured starters, projected gain.",
        "input_schema": {"type": "object", "properties": {"week": WEEK}},
    },
    {
        "name": "matchup_preview",
        "description": "Projected score and win probability for the user's matchup, with both lineups.",
        "input_schema": {"type": "object", "properties": {"week": WEEK}},
    },
    {
        "name": "waiver_targets",
        "description": "Free agents ranked by how much they would improve the user's starting lineup, "
                       "this week and rest of season, with a suggested drop.",
        "input_schema": {
            "type": "object",
            "properties": {
                "position": {"type": "string", "enum": ["QB", "RB", "WR", "TE", "K", "DEF"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25},
            },
        },
    },
    {
        "name": "evaluate_trade",
        "description": "Evaluate a trade by its effect on each side's rest-of-season starting lineup. "
                       "`give` are players on the user's team; `get` are players on the partner's team.",
        "input_schema": {
            "type": "object",
            "properties": {
                "give": {"type": "array", "items": {"type": "string"}, "description": "Player names to send."},
                "get": {"type": "array", "items": {"type": "string"}, "description": "Player names to receive."},
                "partner": {"type": "string", "description": "Trade partner's team or owner name (optional)."},
            },
            "required": ["give", "get"],
        },
    },
    {
        "name": "player_lookup",
        "description": "Find a player in the active league: projections, injury status, and who rosters "
                       "them (or whether they are a free agent).",
        "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
    {
        "name": "trending_players",
        "description": "Players most added or dropped across Sleeper in the last 24 hours, flagged with "
                       "whether they are rostered in the active league.",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["add", "drop"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "recent_transactions",
        "description": "Recent completed adds, drops and trades in the active league.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1}}},
    },
]

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


def tool_handlers(c: Consultant) -> dict[str, Callable[..., Any]]:
    return {
        "list_leagues": c.list_leagues,
        "select_league": c.select_league,
        "league_overview": c.league_overview,
        "my_roster": c.my_roster,
        "team_roster": c.team_roster,
        "optimize_lineup": c.lineup,
        "matchup_preview": c.matchup,
        "waiver_targets": c.waivers,
        "evaluate_trade": c.trade,
        "player_lookup": c.player_lookup,
        "trending_players": c.trending,
        "recent_transactions": c.transactions,
    }


class FantasyAgent:
    def __init__(
        self,
        consultant: Consultant,
        client: anthropic.Anthropic | None = None,
        model: str | None = None,
        effort: str = "medium",
        web_search: bool = True,
        max_tool_rounds: int = 25,
    ):
        self.consultant = consultant
        self.client = client or anthropic.Anthropic()
        self.model = model or consultant.config.model
        self.effort = effort
        self.tools = TOOLS + ([WEB_SEARCH_TOOL] if web_search else [])
        self.handlers = tool_handlers(consultant)
        self.max_tool_rounds = max_tool_rounds
        self.messages: list[dict[str, Any]] = []
        self.system = SYSTEM_PROMPT + f"\n\nToday's date: {dt.date.today().isoformat()}."

    def run_tool(self, name: str, args: dict) -> tuple[str, bool]:
        """Run one tool call; returns (JSON result, is_error)."""
        handler = self.handlers.get(name)
        if handler is None:
            return f"Unknown tool {name!r}", True
        try:
            return json.dumps(handler(**(args or {})), default=str), False
        except (KeyError, LookupError, ValueError, TypeError, RuntimeError) as exc:
            return f"{type(exc).__name__}: {exc}", True

    def ask(self, question: str, on_tool: Callable[[str, dict], None] | None = None) -> str:
        """Send one user message, run tools until Claude answers, and return the answer text."""
        self.messages.append({"role": "user", "content": question})
        for _ in range(self.max_tool_rounds):
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=self.tools,
                messages=self.messages,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                cache_control={"type": "ephemeral"},
                # On a safety-classifier refusal, the API re-runs the request on a fallback model.
                fallbacks="default",
                betas=["server-side-fallback-2026-07-01"],
            )
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return "Sorry, I can't help with that request."
            if response.stop_reason == "pause_turn":
                continue  # server-side web search paused; resend to let it resume
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n\n[Answer was cut off at the token limit.]"
                return text

            results = []
            for block in tool_uses:
                if on_tool:
                    on_tool(block.name, block.input)
                content, is_error = self.run_tool(block.name, block.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
                )
            self.messages.append({"role": "user", "content": results})
        return "I stopped after too many tool calls without reaching an answer; try a narrower question."

    def reset(self) -> None:
        self.messages = []
