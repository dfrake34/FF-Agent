from ffagent import analysis
from ffagent.models import Player, RosterEntry, Team

SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "BN", "BN"]


def P(pid, pos, proj, ros=None, injury=None):
    return Player(id=pid, name=pid, position=pos, projected=proj,
                  ros_projected=ros if ros is not None else proj * 10, injury_status=injury)


def team(players, starters=()):
    return Team(id="1", name="T", owner="o",
                roster=[RosterEntry(p, "FLEX" if p.id in starters else "BN") for p in players])


def test_optimize_fills_dedicated_slots_before_flex():
    players = [P("qb1", "QB", 20), P("qb2", "QB", 18), P("rb1", "RB", 15), P("rb2", "RB", 9),
               P("rb3", "RB", 8), P("wr1", "WR", 14), P("wr2", "WR", 12), P("wr3", "WR", 11),
               P("te1", "TE", 6)]
    lineup = analysis.optimize_lineup(players, SLOTS)
    by_slot = [(s, p.id) for s, p in lineup.starters]
    assert by_slot == [("QB", "qb1"), ("RB", "rb1"), ("RB", "rb2"), ("WR", "wr1"), ("WR", "wr2"),
                       ("TE", "te1"), ("FLEX", "wr3"), ("SUPER_FLEX", "qb2")]
    assert lineup.total == 20 + 15 + 9 + 14 + 12 + 6 + 11 + 18
    assert [p.id for p in lineup.bench] == ["rb3"]


def test_optimize_skips_inactive_players_and_reports_empty_slots():
    players = [P("qb1", "QB", 25, injury="Out"), P("rb1", "RB", 10)]
    lineup = analysis.optimize_lineup(players, ["QB", "RB", "TE"])
    assert lineup.total == 10
    assert lineup.empty_slots == ["TE"]


def test_waiver_targets_rank_by_lineup_gain_and_suggest_drop():
    roster = [P("qb1", "QB", 20), P("rb1", "RB", 12), P("rb2", "RB", 3, ros=10), P("wr1", "WR", 10)]
    fas = [P("fa_rb", "RB", 14), P("fa_te", "TE", 1, ros=5), P("fa_qb", "QB", 5)]
    recs = analysis.waiver_targets(team(roster), fas, ["QB", "RB", "WR", "TE", "BN"])
    assert [r["player"] for r in recs] == ["fa_rb", "fa_te"]  # backup QB adds nothing
    assert recs[0]["weekly_lineup_gain"] == 2
    assert recs[0]["suggested_drop"] == "rb2"


def test_trade_evaluation_is_lineup_aware():
    mine = team([P("qb1", "QB", 20), P("wr1", "WR", 15), P("wr2", "WR", 14), P("wr3", "WR", 13),
                 P("rb1", "RB", 5)])
    partner = Team(id="2", name="Partner", owner="p",
                   roster=[RosterEntry(P("rb9", "RB", 12), "RB"), RosterEntry(P("qb9", "QB", 18), "QB")])
    give = [mine.roster[3].player]  # wr3, who would be benched in a 1-RB/2-WR lineup
    get = [partner.roster[0].player]
    result = analysis.evaluate_trade(mine, give, get, ["QB", "RB", "WR", "WR", "BN"], partner)
    assert result["my_ros_lineup_change"] == 70.0  # rb9 (120) replaces rb1 (50) in the RB slot
    assert result["partner_ros_lineup_change"] == 10.0  # loses rb9 (120), wr3 (130) fills an empty WR slot
    assert result["verdict"] == "accept"


def test_win_probability_is_symmetric():
    a, b = [P("a", "QB", 100)], [P("b", "QB", 80)]
    assert analysis.win_probability(a, a) == 0.5
    assert round(analysis.win_probability(a, b) + analysis.win_probability(b, a), 9) == 1
    assert analysis.win_probability(a, b) > 0.5


def test_standings_order_and_luck():
    teams = [Team(id="1", name="Lucky", owner="a", wins=5, losses=1, points_for=600),
             Team(id="2", name="Unlucky", owner="b", wins=1, losses=5, points_for=800)]
    rows = analysis.standings(teams)
    assert [r["name"] for r in rows] == ["Lucky", "Unlucky"]
    assert rows[0]["luck"] == 5 and rows[1]["luck"] == -5


def test_waivers_never_suggest_dropping_ir_or_taxi_players():
    roster = [RosterEntry(P("rb1", "RB", 12), "RB"), RosterEntry(P("ir", "RB", 0, ros=0), "IR"),
              RosterEntry(P("taxi", "WR", 0, ros=1), "TAXI"), RosterEntry(P("bench", "WR", 2, ros=30), "BN")]
    t = Team(id="1", name="T", owner="o", roster=roster)
    [rec] = analysis.waiver_targets(t, [P("fa", "RB", 15)], ["RB", "BN"])
    assert rec["suggested_drop"] == "bench"
