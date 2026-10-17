from ffagent import cli


def test_cli_lineup_and_trade(monkeypatch, capsys, sleeper_consultant):
    monkeypatch.setattr(cli, "Consultant", lambda config: sleeper_consultant)
    assert cli.main(["lineup"]) == 0
    out = capsys.readouterr().out
    assert "optimal lineup for My Squad: 72" in out and "gain +17" in out

    assert cli.main(["trade", "--give", "Fred Wideout", "--get", "Carl Runner"]) == 0
    assert "Verdict: slight edge" in capsys.readouterr().out


def test_cli_reports_lookup_errors(monkeypatch, capsys, sleeper_consultant):
    monkeypatch.setattr(cli, "Consultant", lambda config: sleeper_consultant)
    assert cli.main(["trade", "--give", "Nobody", "--get", "Carl Runner"]) == 1
    assert "No player matching" in capsys.readouterr().err
