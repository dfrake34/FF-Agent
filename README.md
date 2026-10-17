# FF-Agent

A fantasy football consultant for **Sleeper** and **ESPN** leagues. It pulls your league data
live, runs lineup, waiver, trade and matchup analysis using your league's own scoring and
roster settings, and adds a Claude-powered agent you can chat with ("Who should I start at
flex?", "Is Bijan for Chase a good trade?", "Who's the best RB on waivers?").

## Setup

```bash
pip install -e ".[dev]"
cp .env.example .env   # then fill in your league details
```

| Variable | Needed for |
|---|---|
| `SLEEPER_USERNAME` | Identifying your team, and discovering all your Sleeper leagues |
| `SLEEPER_LEAGUE_ID` | Your Sleeper league (the number in the league URL) |
| `ESPN_LEAGUE_ID` | Your ESPN league (`leagueId=` in the URL) |
| `ESPN_S2`, `ESPN_SWID` | Private ESPN leagues: the `espn_s2` and `SWID` cookies from espn.com (browser DevTools → Application → Cookies). The SWID also identifies your team. |
| `ESPN_TEAM_ID` | Your ESPN team id, if you don't supply `ESPN_SWID` |
| `ANTHROPIC_API_KEY` | The `chat` agent (or run `ant auth login`) |
| `FF_SEASON`, `FF_DEFAULT_PLATFORM`, `FF_MODEL`, `FF_CACHE_DIR` | Optional overrides |

You can configure one league on each platform. Use `--league sleeper:<id>` or `--league espn:<id>` to pick one.

## Usage

```bash
ffagent chat                                  # interactive consultant
ffagent chat "Who should I pick up this week?"
ffagent standings                             # standings + points-for rank + luck
ffagent roster
ffagent lineup --week 8                       # optimal lineup vs. what you have set
ffagent matchup                               # projected score and win probability
ffagent waivers --position RB
ffagent trade --give "Garrett Wilson" --get "Kyren Williams" --partner "Mike"
ffagent trending --kind add                   # Sleeper-wide adds, flagged if available in your league
ffagent leagues                               # configured + discoverable leagues
ffagent --json lineup                         # raw JSON output for any command
```

## How it works

```
ffagent/
  sleeper.py     Sleeper API client + league adapter (public API, no auth)
  espn.py        ESPN fantasy API client + league adapter (cookies for private leagues)
  platforms.py   FantasyLeague interface both adapters implement
  models.py      Platform-neutral League / Team / Player / Matchup models
  analysis.py    Lineup optimizer, waiver ranking, trade evaluator, win probability, standings
  consultant.py  Answers questions across leagues; backs both the CLI and the agent tools
  agent.py       Claude tool-use loop (live league tools + web search for news)
  cli.py         Command-line interface
```

- **Projections:** Sleeper weekly and season projections are scored with your league's
  `scoring_settings`, so TE premium, 6-pt pass TDs and similar settings are reflected. ESPN
  projections come from ESPN's own per-league projected totals.
- **Waivers** leave out kickers and defenses unless you ask for them with `--position K` or `--position DEF`.
- **Lineups:** fills the most restrictive slots first, then FLEX and SUPER_FLEX, and skips
  players ruled Out, on IR or suspended.
- **Waivers:** each free agent is scored by how much they raise your *optimal starting lineup*
  this week and for the rest of the season. The suggested drop is your lowest-value player
  who wouldn't start.
- **Trades:** both sides are compared on rest-of-season starting-lineup value before and after
  the trade, not on raw player totals. A trade that adds a starter beats one that adds a
  better bench player.
- **Matchups:** win probability assumes each player's weekly score varies around their
  projection, and uses a normal approximation of the score difference.
- **Agent:** Claude calls these tools for live data, uses web search for injury and depth-chart
  news, and gives you a clear call. It can't make moves in the apps itself. The default
  model is `claude-opus-5-5` with adaptive thinking. If a request is declined by a safety
  classifier, server-side refusal fallbacks (`fallbacks: "default"`) re-run it on a fallback model.

Data is cached in `~/.cache/ffagent`. Sleeper's player database is fetched at most once a day,
as Sleeper asks.

## Notes and limits

- ESPN's API and Sleeper's projections endpoint are undocumented and may change.
- On Sleeper, rest-of-season values add up Sleeper's weekly projections through week 17, so
  bye weeks count as zero. If future weeks aren't published yet, and always on ESPN, they're
  season projections scaled to the weeks remaining.
- Opponents and bye weeks come from Sleeper's projections and are only shown for Sleeper leagues.
- The tools only read your leagues. They don't set lineups or submit claims or trades.

## Tests

```bash
pytest
```

Tests run against canned Sleeper/ESPN payloads and a fake Claude client. They need no network access.
