"""The LLM site-match fallback.

This runs only when basic_site_match() fails, and whatever it returns is checked
against the options ServiceNow actually offered — so the property that matters is
that a wrong, invented, or unavailable answer becomes None rather than a bad ticket.
"""
import gn_ticket


OPTIONS = ["Iqaluit Nakasuk School", "Rankin Inlet Maani Ulujuk School", "Igloolik High School, Desktop Unit"]


class FakeResponse:
    def __init__(self, status_code=200, content=None, text=""):
        self.status_code = status_code
        self._content = content
        self.text = text

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _capture(monkeypatch, response):
    """Swap the HTTP call for a recorder, returning the captured request body."""
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["url"] = url
        sent["headers"] = headers
        sent["json"] = json
        sent["timeout"] = timeout
        return response

    monkeypatch.setattr(gn_ticket.requests, "post", fake_post)
    return sent


def test_a_valid_suggestion_is_returned(monkeypatch):
    _capture(monkeypatch, FakeResponse(content="Iqaluit Nakasuk School"))

    result = gn_ticket.ask_chatgpt_for_best_match(OPTIONS, "Iqaluit", "Nakasuk School", api_key="sk-test")

    assert result == "Iqaluit Nakasuk School"


def test_matching_ignores_case_and_padding(monkeypatch):
    _capture(monkeypatch, FakeResponse(content="  iqaluit nakasuk school  "))

    result = gn_ticket.ask_chatgpt_for_best_match(OPTIONS, "Iqaluit", "Nakasuk School", api_key="sk-test")

    assert result == "Iqaluit Nakasuk School"


def test_an_invented_option_is_rejected(monkeypatch):
    """The safety property: never return something ServiceNow did not offer."""
    _capture(monkeypatch, FakeResponse(content="Some School That Does Not Exist"))

    result = gn_ticket.ask_chatgpt_for_best_match(OPTIONS, "Iqaluit", "Nakasuk School", api_key="sk-test")

    assert result is None


def test_an_api_error_returns_none(monkeypatch):
    """A retired model ID lands here as a 404 — it must not raise into the booking loop."""
    _capture(monkeypatch, FakeResponse(status_code=404, text="model not found"))

    result = gn_ticket.ask_chatgpt_for_best_match(OPTIONS, "Iqaluit", "Nakasuk School", api_key="sk-test")

    assert result is None


def test_a_network_failure_returns_none(monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(gn_ticket.requests, "post", boom)

    result = gn_ticket.ask_chatgpt_for_best_match(OPTIONS, "Iqaluit", "Nakasuk School", api_key="sk-test")

    assert result is None


def test_no_api_key_skips_the_call(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("must not call the API without a key")

    monkeypatch.setattr(gn_ticket.requests, "post", fail)

    assert gn_ticket.ask_chatgpt_for_best_match(OPTIONS, "Iqaluit", "Nakasuk School", api_key=None) is None
    assert gn_ticket.ask_chatgpt_for_best_match([], "Iqaluit", "Nakasuk School", api_key="sk-test") is None


def test_gpt5_family_payload_omits_temperature(monkeypatch):
    """GPT-5 rejects temperature and renamed max_tokens; a bare string swap would 400."""
    monkeypatch.setattr(gn_ticket, "CHATGPT_MODEL", "gpt-5-mini")
    sent = _capture(monkeypatch, FakeResponse(content="Iqaluit Nakasuk School"))

    gn_ticket.ask_chatgpt_for_best_match(OPTIONS, "Iqaluit", "Nakasuk School", api_key="sk-test")

    assert sent["json"]["model"] == "gpt-5-mini"
    assert sent["json"]["max_completion_tokens"] == 100
    assert "max_tokens" not in sent["json"]
    assert "temperature" not in sent["json"]


def test_earlier_models_keep_the_legacy_payload(monkeypatch):
    monkeypatch.setattr(gn_ticket, "CHATGPT_MODEL", "gpt-4o-mini")
    sent = _capture(monkeypatch, FakeResponse(content="Iqaluit Nakasuk School"))

    gn_ticket.ask_chatgpt_for_best_match(OPTIONS, "Iqaluit", "Nakasuk School", api_key="sk-test")

    assert sent["json"]["model"] == "gpt-4o-mini"
    assert sent["json"]["max_tokens"] == 100
    assert sent["json"]["temperature"] == 0.1
    assert "max_completion_tokens" not in sent["json"]


def test_the_school_and_options_reach_the_prompt(monkeypatch):
    sent = _capture(monkeypatch, FakeResponse(content="Iqaluit Nakasuk School"))

    gn_ticket.ask_chatgpt_for_best_match(OPTIONS, "Iqaluit", "Nakasuk School", api_key="sk-test")

    prompt = sent["json"]["messages"][0]["content"]
    assert "Nakasuk School" in prompt
    assert "Iqaluit" in prompt
    for option in OPTIONS:
        assert option in prompt
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
