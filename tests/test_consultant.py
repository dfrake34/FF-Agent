import pytest

from ffagent.consultant import find_player
from ffagent.models import Player


def test_find_player_handles_suffixes_partials_and_typos():
    players = [Player("1", "Hal Wideout Jr.", "WR"), Player("2", "Gus Wideout", "WR")]
    assert find_player("hal wideout", players).id == "1"
    assert find_player("Gus", players).id == "2"
    assert find_player("Gus Wideot", players).id == "2"
    with pytest.raises(KeyError, match="ambiguous"):
        find_player("wideout", players)


def test_lineup_advice(sleeper_consultant):
    advice = sleeper_consultant.lineup()
    assert advice["optimal_lineup"]["projected_total"] == 72
    assert advice["current_projected_total"] == 55
    assert sorted(advice["start"]) == ["Fred Wideout", "Hal Wideout Jr."]
    assert sorted(advice["bench"]) == ["Eli Runner", "Gus Wideout"]
    assert advice["injured_starters"] == ["Eli Runner"]


def test_matchup_and_waivers(sleeper_consultant):
    m = sleeper_consultant.matchup()
    assert m["opponent"] == "rival" and m["my_projected"] == 72 and m["opponent_projected"] == 41
    assert m["win_probability"] > 0.9
    w = sleeper_consultant.waivers()
    assert w["recommendations"][0]["player"] == "Free Agent Back"
    assert w["recommendations"][0]["weekly_lineup_gain"] == 1


def test_trade_finds_partner_automatically(sleeper_consultant):
    result = sleeper_consultant.trade(["Fred Wideout"], ["Carl Runner"])
    assert result["partner"] == "rival"
    assert result["my_ros_lineup_change"] == 11
    assert result["verdict"] == "slight edge"


def test_player_lookup(sleeper_consultant):
    assert sleeper_consultant.player_lookup("carl runner")["rostered_by"] == "rival"
    assert sleeper_consultant.player_lookup("Free Agent Back")["available"] is True


def test_overview(sleeper_consultant):
    o = sleeper_consultant.league_overview()
    assert o["my_team_id"] == "1"
    assert [r["name"] for r in o["standings"]] == ["My Squad", "rival"]
