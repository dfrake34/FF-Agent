"""Configuration from environment variables (optionally loaded from a .env file)."""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines; existing environment variables win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def default_season(today: dt.date | None = None) -> int:
    # The NFL season that starts in September runs into January/February of the next year.
    today = today or dt.date.today()
    return today.year if today.month >= 3 else today.year - 1


@dataclass
class Config:
    season: int
    default_platform: str | None = None
    sleeper_username: str | None = None
    sleeper_league_id: str | None = None
    espn_league_id: str | None = None
    espn_team_id: str | None = None
    espn_s2: str | None = None
    espn_swid: str | None = None
    cache_dir: str = "~/.cache/ffagent"
    model: str = "claude-opus-5-5"

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        env = lambda k: os.environ.get(k) or None  # noqa: E731
        swid = env("ESPN_SWID")
        if swid and not swid.startswith("{"):
            swid = "{" + swid.strip("{}") + "}"
        return cls(
            season=int(env("FF_SEASON") or default_season()),
            default_platform=env("FF_DEFAULT_PLATFORM"),
            sleeper_username=env("SLEEPER_USERNAME"),
            sleeper_league_id=env("SLEEPER_LEAGUE_ID"),
            espn_league_id=env("ESPN_LEAGUE_ID"),
            espn_team_id=env("ESPN_TEAM_ID"),
            espn_s2=env("ESPN_S2"),
            espn_swid=swid,
            cache_dir=env("FF_CACHE_DIR") or "~/.cache/ffagent",
            model=env("FF_MODEL") or "claude-opus-5-5",
        )
