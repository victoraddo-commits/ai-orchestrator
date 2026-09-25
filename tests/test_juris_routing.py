"""Phase E Task 2 — small-model routing for simple queries.

Hermetic: no real Ollama/Telegram/legal-brain calls. The streaming transport is
monkeypatched. The routing classifier is a pure, zero-network-call heuristic
(never a second LLM invocation), so these tests exercise it directly.
"""

import json

import pytest

from core.juris_kai import routing as jrouting
from core.juris_kai import streaming as jstream


@pytest.fixture(autouse=True)
def _routing_forced_on(monkeypatch):
    """Exercise the downshift path deterministically.

    The shipped default is ``on``; these tests also pin it explicitly so the
    classifier and wiring are tested without a live GPU probe. The cautious
    ``auto`` residency gate is covered by ``TestAutoGate``.
    """
    monkeypatch.setenv("JURIS_KAI_ROUTING", "on")
    monkeypatch.delenv("JURIS_KAI_FORCE_MODEL", raising=False)
    monkeypatch.delenv("JURIS_KAI_SMALL_MODEL", raising=False)
    monkeypatch.delenv("JURIS_KAI_SMALL_VIABLE", raising=False)
    yield


SIMPLE = "What is Article 19 of the 1992 Constitution?"
COMPLEX_KW = "Explain the fundamental human rights provisions in detail"
LONG = ("I run a small haulage business in Kumasi and my driver was stopped by "
        "the police who demanded money before releasing the truck, so please set "
        "out what my rights and options are under the law of Ghana")
MULTI = "What is a contract, what makes it valid, and when can it be set aside"


class TestClassifier:
    def test_short_lookup_routes_to_small(self):
        r = jrouting.route(SIMPLE, task_type="juris_research")
        assert r.small is True
        assert r.complexity == "simple"
        assert r.model == jrouting.small_model()

    def test_complex_keyword_routes_to_strong(self):
        r = jrouting.route(COMPLEX_KW, task_type="juris_research")
        assert r.small is False
        assert r.complexity == "complex"
        assert "explain" in r.reason
        assert r.model == jrouting.strong_model()

    def test_long_query_routes_to_strong(self):
        r = jrouting.route(LONG, task_type="juris_research")
        assert r.small is False

    def test_multi_clause_routes_to_strong(self):
        r = jrouting.route(MULTI, task_type="juris_research")
        assert r.small is False

    def test_deep_flag_routes_to_strong(self):
        r = jrouting.route(SIMPLE, task_type="juris_research", deep=True)
        assert r.small is False

    def test_deep_task_type_never_small(self):
        for task in ("juris_advocate", "juris_opponent", "juris_judge",
                     "juris_advocate_fast", "juris_case_analysis",
                     "juris_argument_construction"):
            r = jrouting.route(SIMPLE, task_type=task)
            assert r.small is False, task

    def test_empty_query_is_safe_strong(self):
        r = jrouting.route("", task_type="juris_research")
        assert r.small is False

    def test_long_prompt_without_query_is_strong(self):
        # Deep/single-pass callers pass a long grounded prompt but no raw query;
        # the classifier must not treat a big context blob as "simple".
        r = jrouting.route(None, task_type="juris_research",
                           prompt="SOURCE 1: " + ("x" * 4000))
        assert r.small is False


class TestClassify:
    """The pure ``classify`` label (independent of routing mode/residency)."""

    @pytest.mark.parametrize("query", [
        "What is bail?",
        "What is Article 19 of the 1992 Constitution?",
        "Define stealing",
    ])
    def test_short_lookups_are_simple(self, query):
        assert jrouting.classify(query) == "simple"

    @pytest.mark.parametrize("query", [
        "Explain the fundamental human rights provisions",
        "Analyse the elements of the offence",
        "Compare a contract and a will",
        "Discuss the effect of the 2021 amendment",
        "Draft a demand letter",
        "Why is bail granted?",
        "How does the court assess unreasonableness?",
        "Construct an argument that the search was unlawful",
    ])
    def test_analysis_verbs_are_complex(self, query):
        assert jrouting.classify(query) == "complex"

    def test_multi_part_question_is_complex(self):
        assert jrouting.classify(
            "What is a contract, what makes it valid, and when is it void"
        ) == "complex"
        assert jrouting.classify("Is it lawful? What are my options?") == \
            "complex"

    def test_long_query_is_complex(self):
        assert jrouting.classify(LONG) == "complex"

    def test_empty_is_complex(self):
        assert jrouting.classify("") == "complex"
        assert jrouting.classify(None) == "complex"

    def test_deep_flag_and_deep_task_type_are_complex(self):
        assert jrouting.classify("What is bail?", deep=True) == "complex"
        assert jrouting.classify(
            "What is bail?", task_type="juris_advocate") == "complex"

    def test_classify_ignores_routing_mode(self, monkeypatch):
        # It labels the query, not the route: even with routing disabled the
        # query shape is still "simple".
        monkeypatch.setenv("JURIS_KAI_ROUTING", "off")
        assert jrouting.classify("What is bail?") == "simple"


class TestAutoGate:
    def test_default_mode_is_auto(self, monkeypatch):
        monkeypatch.delenv("JURIS_KAI_ROUTING", raising=False)
        assert jrouting.routing_mode() == "auto"

    def test_auto_uses_small_when_viable(self, monkeypatch):
        monkeypatch.setenv("JURIS_KAI_ROUTING", "auto")
        monkeypatch.setattr(jrouting, "_probe_small_resident", lambda m: True)
        r = jrouting.route(SIMPLE, task_type="juris_research")
        assert r.small is True

    def test_auto_stays_strong_when_not_viable(self, monkeypatch):
        monkeypatch.setenv("JURIS_KAI_ROUTING", "auto")
        monkeypatch.setattr(jrouting, "_probe_small_resident", lambda m: False)
        r = jrouting.route(SIMPLE, task_type="juris_research")
        assert r.small is False
        assert r.model == jrouting.strong_model()
        assert "not-viable" in r.reason

    def test_small_viable_env_override(self, monkeypatch):
        monkeypatch.setenv("JURIS_KAI_ROUTING", "auto")
        monkeypatch.setenv("JURIS_KAI_SMALL_VIABLE", "1")
        monkeypatch.setattr(jrouting, "_probe_small_resident", lambda m: False)
        r = jrouting.route(SIMPLE, task_type="juris_research")
        assert r.small is True

    def test_viable_false_when_small_equals_strong(self, monkeypatch):
        monkeypatch.setenv("JURIS_KAI_SMALL_MODEL", jrouting.strong_model())
        monkeypatch.setenv("JURIS_KAI_SMALL_VIABLE", "1")
        assert jrouting.small_model_viable() is False

    def test_partially_offloaded_model_is_not_viable(self, monkeypatch):
        monkeypatch.delenv("JURIS_KAI_SMALL_VIABLE", raising=False)
        payload = json.dumps({"models": [{
            "name": jrouting.small_model(),
            "size": 1731995891,
            "size_vram": 848004382,
        }]}).encode()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return payload

        monkeypatch.setattr(jrouting.urllib.request, "urlopen",
                            lambda url, timeout=None: _Resp())
        jrouting._VIABILITY_CACHE.clear()
        assert jrouting._probe_small_resident(jrouting.small_model()) is False

    def test_fully_resident_model_is_viable(self, monkeypatch):
        monkeypatch.delenv("JURIS_KAI_SMALL_VIABLE", raising=False)
        payload = json.dumps({"models": [{
            "name": jrouting.small_model(),
            "size": 1731995891,
            "size_vram": 1731995891,
        }]}).encode()

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return payload

        monkeypatch.setattr(jrouting.urllib.request, "urlopen",
                            lambda url, timeout=None: _Resp())
        jrouting._VIABILITY_CACHE.clear()
        assert jrouting._probe_small_resident(jrouting.small_model()) is True


class TestOverrides:
    def test_routing_disabled_always_strong(self, monkeypatch):
        monkeypatch.setenv("JURIS_KAI_ROUTING", "off")
        r = jrouting.route(SIMPLE, task_type="juris_research")
        assert r.model == jrouting.strong_model()
        assert r.small is False
        assert "disabled" in r.reason

    def test_force_model_wins(self, monkeypatch):
        monkeypatch.setenv("JURIS_KAI_FORCE_MODEL", "custom:model")
        r = jrouting.route(SIMPLE, task_type="juris_research")
        assert r.model == "custom:model"
        assert r.small is False

    def test_small_model_env_respected(self, monkeypatch):
        monkeypatch.setenv("JURIS_KAI_SMALL_MODEL", "tiny:1b")
        r = jrouting.route(SIMPLE, task_type="juris_research")
        assert r.model == "tiny:1b"

    def test_select_model_records_decision(self, monkeypatch):
        jrouting._RECENT.clear()
        model = jrouting.select_model(SIMPLE, task_type="juris_research")
        assert model == jrouting.small_model()
        recent = jrouting.recent_routes()
        assert recent and recent[-1]["model"] == model
        assert recent[-1]["complexity"] == "simple"


class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):
        for line in self._lines:
            yield line

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_response(text="ok"):
    import json
    return _FakeStreamResponse([
        json.dumps({"message": {"content": text}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ])


class TestStreamingRouting:
    def test_stream_chat_uses_small_model_for_simple_query(self, monkeypatch):
        seen = {}

        def fake_post(url, json=None, stream=None, timeout=None):
            seen["model"] = json["model"]
            return _ok_response()

        monkeypatch.setattr(jstream.requests, "post", fake_post)
        out = "".join(jstream.stream_chat(SIMPLE, task_type="juris_research",
                                          query=SIMPLE))
        assert out == "ok"
        assert seen["model"] == jrouting.small_model()

    def test_stream_chat_uses_strong_model_for_complex_query(self, monkeypatch):
        seen = {}

        def fake_post(url, json=None, stream=None, timeout=None):
            seen["model"] = json["model"]
            return _ok_response()

        monkeypatch.setattr(jstream.requests, "post", fake_post)
        list(jstream.stream_chat(COMPLEX_KW, task_type="juris_research",
                                 query=COMPLEX_KW))
        assert seen["model"] == jrouting.strong_model()

    def test_explicit_model_overrides_routing(self, monkeypatch):
        seen = {}

        def fake_post(url, json=None, stream=None, timeout=None):
            seen["model"] = json["model"]
            return _ok_response()

        monkeypatch.setattr(jstream.requests, "post", fake_post)
        list(jstream.stream_chat(SIMPLE, task_type="juris_research",
                                 model="pinned:model", query=SIMPLE))
        assert seen["model"] == "pinned:model"

    def test_small_model_failure_falls_back_to_strong(self, monkeypatch):
        calls = []

        def fake_post(url, json=None, stream=None, timeout=None):
            calls.append(json["model"])
            if len(calls) == 1:
                raise RuntimeError("small model down")
            return _ok_response("recovered")

        monkeypatch.setattr(jstream.requests, "post", fake_post)
        out = "".join(jstream.stream_chat(SIMPLE, task_type="juris_research",
                                          query=SIMPLE))
        assert out == "recovered"
        assert calls == [jrouting.small_model(), jrouting.strong_model()]

    def test_guard_still_applied_when_routed_small(self, monkeypatch):
        seen = {"guarded": False}

        def fake_guard(prompt, source):
            seen["guarded"] = True
            return prompt

        def fake_post(url, json=None, stream=None, timeout=None):
            return _ok_response()

        monkeypatch.setattr(jstream, "_guard_prompt", fake_guard)
        monkeypatch.setattr(jstream.requests, "post", fake_post)
        list(jstream.stream_chat(SIMPLE, task_type="juris_research",
                                 query=SIMPLE))
        assert seen["guarded"] is True

    def test_generate_passes_routing_through(self, monkeypatch):
        seen = {}

        def fake_post(url, json=None, stream=None, timeout=None):
            seen["model"] = json["model"]
            return _ok_response("answer")

        monkeypatch.setattr(jstream.requests, "post", fake_post)
        text = jstream.generate(SIMPLE, task_type="juris_research", query=SIMPLE)
        assert text == "answer"
        assert seen["model"] == jrouting.small_model()

    def test_stream_payload_keeps_model_resident(self, monkeypatch):
        seen = {}

        def fake_post(url, json=None, stream=None, timeout=None):
            seen["json"] = json
            return _ok_response()

        monkeypatch.setattr(jstream.requests, "post", fake_post)
        list(jstream.stream_chat(SIMPLE, task_type="juris_research",
                                 query=SIMPLE))
        assert jstream.KEEP_ALIVE
        assert seen["json"]["keep_alive"] == jstream.KEEP_ALIVE


class TestDeepPath:
    """The Deep reasoning module must never downshift, whatever the prompt."""

    def test_reasoning_blocking_generate_is_30b_pinned(self, monkeypatch):
        from core.juris_kai import reasoning as jreasoning

        seen = {}

        def fake_generate(prompt, task_type="legal_research", **kwargs):
            seen["prompt"] = prompt
            seen["task_type"] = task_type
            seen.update(kwargs)
            return "grounded"

        monkeypatch.setattr(jstream, "generate", fake_generate)
        out = jreasoning._generate("short grounded prompt", "juris_advocate")
        assert out == "grounded"
        assert seen["deep"] is True
        assert seen["task_type"] == "juris_advocate"

    def test_reasoning_stream_generate_is_30b_pinned(self, monkeypatch):
        from core.juris_kai import reasoning as jreasoning

        seen = {}

        def fake_stream(prompt, task_type="legal_research", **kwargs):
            seen["prompt"] = prompt
            seen["task_type"] = task_type
            seen.update(kwargs)
            return iter(["grounded"])

        monkeypatch.setattr(jstream, "stream_chat", fake_stream)
        list(jreasoning._stream_generate("short prompt", "juris_judge"))
        assert seen["deep"] is True
        assert seen["task_type"] == "juris_judge"

    def test_deep_prompt_routes_strong_even_if_short(self):
        # Belt-and-braces: ``deep=True`` wins even when the prompt/query shape
        # would otherwise be classified simple.
        r = jrouting.route("What is bail?", task_type="juris_advocate",
                           deep=True)
        assert r.small is False
        assert r.model == jrouting.strong_model()


class TestBotWiring:
    def test_stream_to_telegram_returns_routed_model(self, monkeypatch):
        from core.juris_kai import bot as jbot
        monkeypatch.setattr(jbot, "_send_placeholder", lambda chat_id: 42)
        monkeypatch.setattr(
            jbot._streaming, "stream_chat",
            lambda prompt, task_type="legal_research", **k: iter(["answer"]))
        monkeypatch.setattr(jbot, "_edit_message_text", lambda *a, **k: {"ok": True})
        monkeypatch.setattr(jbot, "_finalize_stream", lambda *a, **k: None)

        text, model, delivered = jbot._stream_to_telegram(
            "grounded prompt", "juris_research", 1, None, query=SIMPLE)
        assert delivered is True
        assert model == jrouting.small_model()

    def test_stream_to_telegram_complex_stays_strong(self, monkeypatch):
        from core.juris_kai import bot as jbot
        monkeypatch.setattr(jbot, "_send_placeholder", lambda chat_id: 42)
        monkeypatch.setattr(
            jbot._streaming, "stream_chat",
            lambda prompt, task_type="legal_research", **k: iter(["answer"]))
        monkeypatch.setattr(jbot, "_edit_message_text", lambda *a, **k: {"ok": True})
        monkeypatch.setattr(jbot, "_finalize_stream", lambda *a, **k: None)

        _text, model, _delivered = jbot._stream_to_telegram(
            "grounded prompt", "juris_research", 1, None, query=COMPLEX_KW)
        assert model == jrouting.strong_model()
