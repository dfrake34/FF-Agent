import json
from types import SimpleNamespace as NS

from ffagent.agent import TOOLS, FantasyAgent, tool_handlers


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


def fake_client(*responses):
    return NS(beta=NS(messages=FakeMessages(responses)))


def text(t):
    return NS(type="text", text=t)


def tool_use(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


def test_every_tool_has_a_handler(sleeper_consultant):
    assert {t["name"] for t in TOOLS} == set(tool_handlers(sleeper_consultant))


def test_agent_runs_tools_and_returns_answer(sleeper_consultant):
    client = fake_client(
        NS(stop_reason="tool_use", content=[tool_use("t1", "optimize_lineup", {}),
                                            tool_use("t2", "player_lookup", {"name": "Nobody Atall"})]),
        NS(stop_reason="end_turn", content=[text("Start Fred Wideout.")]),
    )
    agent = FantasyAgent(sleeper_consultant, client=client, web_search=False)
    seen = []
    answer = agent.ask("Who should I start?", on_tool=lambda n, a: seen.append(n))

    assert answer == "Start Fred Wideout."
    assert seen == ["optimize_lineup", "player_lookup"]
    first, second = client.beta.messages.requests
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert first["thinking"] == {"type": "adaptive"}
    # Both tool results go back in a single user message.
    results = second["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert json.loads(results[0]["content"])["projected_gain"] == 17
    assert results[1]["is_error"] is False and "error" in json.loads(results[1]["content"])


def test_tool_errors_are_reported_not_raised(sleeper_consultant):
    agent = FantasyAgent(sleeper_consultant, client=fake_client(), web_search=False)
    content, is_error = agent.run_tool("team_roster", {"team": "no such team"})
    assert is_error and "No team matches" in content
    content, is_error = agent.run_tool("nope", {})
    assert is_error


def test_refusal_and_pause_turn(sleeper_consultant):
    client = fake_client(
        NS(stop_reason="pause_turn", content=[NS(type="server_tool_use", id="s1")]),
        NS(stop_reason="end_turn", content=[text("News checked.")]),
        NS(stop_reason="refusal", content=[]),
    )
    agent = FantasyAgent(sleeper_consultant, client=client)
    assert agent.ask("Any injury news?") == "News checked."
    assert len(client.beta.messages.requests) == 2
    assert any(t.get("type") == "web_search_20260209" for t in client.beta.messages.requests[0]["tools"])
    assert agent.ask("something else") == "Sorry, I can't help with that request."
