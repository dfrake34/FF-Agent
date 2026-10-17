import json


def test_league_settings(espn_league):
    lg = espn_league.league()
    assert lg.name == "ESPN Test" and lg.scoring == "Half PPR" and lg.current_week == 7
    assert lg.roster_slots == ["QB", "RB", "WR", "TE", "FLEX", "BN", "BN", "BN"]


def test_private_league_sends_cookies(espn_league):
    espn_league.league()
    assert espn_league.client.cookies == {"espn_s2": "s2", "SWID": "{SWID-1}"}


def test_teams_and_my_team(espn_league):
    assert espn_league.my_team_id() == "1"
    me = espn_league.my_team()
    assert (me.name, me.owner, me.record, me.points_for) == ("Espn Mine", "espnme", "5-1", 800.5)
    rb = next(e for e in me.roster if e.player.name == "Rb One")
    assert (rb.slot, rb.player.position, rb.player.team, rb.player.injury_status) == ("RB", "RB", "SF", "OUT")
    qb = me.roster[0].player
    assert qb.projected == 20 and qb.ros_projected == 220.0
    assert espn_league.team("2").name == "Rival Town"


def test_matchups_for_current_week(espn_league):
    [m] = espn_league.matchups()
    assert (m.home_team_id, m.away_team_id) == ("2", "1")


def test_free_agents_use_fantasy_filter(espn_league):
    [k] = espn_league.free_agents("K")
    assert (k.name, k.position, k.percent_owned) == ("Fa Kicker", "K", 12.3)
    url, params, headers = espn_league.client.http.calls[-1]
    flt = json.loads(headers["x-fantasy-filter"])["players"]
    assert flt["filterStatus"]["value"] == ["FREEAGENT", "WAIVERS"]
    assert flt["filterSlotIds"]["value"] == [17]
