from ffagent.sleeper import describe_scoring, score_stats


def test_score_stats_uses_league_scoring_and_falls_back_to_pts_ppr():
    assert score_stats({"rec": 5, "rec_yd": 50, "rec_td": 1}, {"rec": 1, "rec_yd": 0.1, "rec_td": 6}) == 16
    assert score_stats({"pts_half_ppr": 9.5}, {"rec": 0.5}) == 9.5
    assert describe_scoring({"rec": 0.5, "pass_td": 6}) == "Half PPR, 6pt pass TD"


def test_league_and_teams(sleeper_league):
    lg = sleeper_league.league()
    assert (lg.name, lg.season, lg.current_week, lg.scoring) == ("Test League", 2026, 7, "PPR")
    assert lg.starting_slots == ["QB", "RB", "WR", "TE", "FLEX"]
    assert sleeper_league.my_team_id() == "1"

    me = sleeper_league.my_team()
    assert me.name == "My Squad" and me.record == "4-2" and me.points_for == 700.5
    slots = {e.player.name: e.slot for e in me.roster}
    assert slots["Eli Runner"] == "RB" and slots["Dan Runner"] == "FLEX" and slots["Fred Wideout"] == "BN"
    eli = next(e.player for e in me.roster if e.player.name == "Eli Runner")
    assert eli.projected == 14 and eli.injury_status == "Out"
    assert eli.ros_projected == 154  # 14 * 17 season points * 11 of 17 weeks left

    rival = sleeper_league.team("2")
    assert [e.slot for e in rival.roster] == ["QB", "RB", "TE"]


def test_matchups_pair_rosters(sleeper_league):
    [m] = sleeper_league.matchups()
    assert (m.week, m.home_team_id, m.away_team_id) == (7, "1", "2")


def test_free_agents_exclude_rostered_and_inactive(sleeper_league):
    assert [p.name for p in sleeper_league.free_agents()] == ["Free Agent Back", "Free Agent Wideout"]
    assert [p.name for p in sleeper_league.free_agents("WR")] == ["Free Agent Wideout"]


def test_trending_flags_rostered(sleeper_league):
    trending = sleeper_league.trending()
    assert [(t["name"], t["rostered_in_league"]) for t in trending] == [
        ("Free Agent Back", False), ("Carl Runner", True)]


def test_ros_sums_weekly_projections_and_detects_byes():
    from conftest import SLEEPER_ROUTES, FakeHttp
    from ffagent.sleeper import SleeperClient, SleeperLeague

    routes = dict(SLEEPER_ROUTES)
    for week in range(7, 18):
        # Week 9 is SF's bye: no projection rows for SF players, everyone else plays.
        routes[f"/projections/nfl/2026/{week}"] = [
            {"player_id": "rb1", "team": "SF", "opponent": "SEA", "stats": {"rec": 5, "rec_yd": 50}},
            {"player_id": "rb2", "team": "DET", "opponent": "GB", "stats": {"rec": 1, "rec_yd": 10}},
        ][week == 9:]
    league = SleeperLeague("L1", SleeperClient(FakeHttp(routes)), username="me")

    carl = next(e.player for e in league.team("2").roster if e.player.name == "Carl Runner")
    assert carl.projected == 10 and carl.opponent == "SEA"
    assert carl.ros_projected == 100  # 10 points in each of weeks 7-17 except the week 9 bye
    carl_wk9 = next(e.player for e in league.team("2", week=9).roster if e.player.name == "Carl Runner")
    assert carl_wk9.opponent == "BYE" and carl_wk9.projected == 0
