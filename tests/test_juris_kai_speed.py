"""Part A speed-layer tests: TTL cache, prompt budgets, streaming.

All tests are hermetic — no real Ollama, Telegram, or legal-brain calls. The
streaming transport and Telegram API are monkeypatched, and network-touching
providers are disabled by the suite-wide conftest.
"""

import time

import pytest

from core.juris_kai import cache as jcache
from core.juris_kai import streaming as jstream
from core.juris_kai import prompt as jprompt


@pytest.fixture(autouse=True)
def _clean_caches():
    jcache.GENERATION_CACHE.clear()
    jcache.RETRIEVAL_CACHE.clear()
    yield
    jcache.GENERATION_CACHE.clear()
    jcache.RETRIEVAL_CACHE.clear()


# ── TTLCache ──────────────────────────────────────────────────────────────

class TestTTLCache:
    def test_set_get_roundtrip(self):
        c = jcache.TTLCache(maxsize=4, ttl=60, name="t")
        c.set("a", 1)
        assert c.get("a") == 1
        assert c.get("missing") is None

    def test_expiry(self):
        c = jcache.TTLCache(maxsize=4, ttl=0.05, name="t")
        c.set("a", 1)
        time.sleep(0.08)
        assert c.get("a") is None
        assert c.stats()["expirations"] == 1

    def test_lru_eviction(self):
        c = jcache.TTLCache(maxsize=2, ttl=60, name="t")
        c.set("a", 1)
        c.set("b", 2)
        c.get("a")  # touch a so b is now LRU
        c.set("c", 3)
        assert c.get("b") is None
        assert c.get("a") == 1
        assert c.get("c") == 3
        assert c.stats()["evictions"] == 1

    def test_clear_and_stats(self):
        c = jcache.TTLCache(maxsize=4, ttl=60, name="t")
        c.set("a", 1)
        c.set("b", 2)
        assert c.get("a") == 1
        st = c.stats()
        assert st["size"] == 2 and st["hits"] == 1 and st["sets"] == 2
        assert c.clear() == 2
        assert c.stats()["size"] == 0

    def test_hit_rate(self):
        c = jcache.TTLCache(maxsize=4, ttl=60, name="t")
        c.set("a", 1)
        c.get("a")
        c.get("nope")
        assert c.stats()["hit_rate"] == 0.5


# ── key normalization ─────────────────────────────────────────────────────

class TestKeys:
    def test_normalize_folds_case_whitespace_punctuation(self):
        assert jcache.normalize_query("  What is  CONTRACT Law?! ") == \
            "what is contract law"
        assert jcache.normalize_query("a\t b\n c") == "a b c"

    def test_generation_key_includes_task_query_version(self):
        k1 = jcache.generation_key("juris_research", "Contract Law", "docs=1")
        k2 = jcache.generation_key("juris_research", "contract  law", "docs=1")
        k3 = jcache.generation_key("juris_research", "Contract Law", "docs=2")
        k4 = jcache.generation_key("juris_teaching", "Contract Law", "docs=1")
        assert k1 == k2
        assert k1 != k3
        assert k1 != k4

    def test_retrieval_key(self):
        assert jcache.retrieval_key("Murder!", 3) == ("murder", 3)


# ── prompt budgets ────────────────────────────────────────────────────────

class TestPromptBudget:
    def test_flashcards_shorter_than_research(self):
        assert jprompt.budget_for("legal_flashcards") < \
            jprompt.budget_for("legal_research")
        assert jprompt.budget_for("juris_legal_teaching") < \
            jprompt.budget_for("juris_research")

    def test_unknown_defaults(self):
        assert jprompt.budget_for("no_such_task") == jprompt.DEFAULT_MAX_TOKENS
        assert jprompt.budget_for("") == jprompt.DEFAULT_MAX_TOKENS


# ── legal-context trimming + caching ──────────────────────────────────────

class TestContextTrimAndCache:
    def _docs(self, n, size):
        return [{"title": f"d{i}", "category": "act", "court": "",
                 "year": 2020, "citation": "", "jurisdiction": "Ghana",
                 "chunk_content": "x" * size} for i in range(n)]

    def test_rank_and_trim_caps_chunks(self):
        from core.juris_kai import legal_context as lc
        out = lc._rank_and_trim(self._docs(6, 10), limit=3)
        assert len(out) == 3

    def test_rank_and_trim_caps_total_chars(self):
        from core.juris_kai import legal_context as lc
        out = lc._rank_and_trim(self._docs(10, 2000), limit=10)
        total = sum(len(d["chunk_content"]) for d in out)
        assert total <= lc.MAX_CONTEXT_CHARS + lc.MAX_CHUNK_LENGTH
        assert len(out) < 10

    def test_query_knowledge_base_caches(self, monkeypatch):
        from core.juris_kai import legal_context as lc
        calls = {"n": 0}

        def fake_uncached(query, limit=3):
            calls["n"] += 1
            return self._docs(2, 20)

        monkeypatch.setattr(lc, "_query_knowledge_base_uncached", fake_uncached)
        first = lc.query_knowledge_base("Contract law")
        second = lc.query_knowledge_base("contract  law!")  # normalized same
        assert calls["n"] == 1
        assert first == second
        assert jcache.RETRIEVAL_CACHE.stats()["hits"] >= 1

    def test_query_knowledge_base_distinct_queries_miss(self, monkeypatch):
        from core.juris_kai import legal_context as lc
        calls = {"n": 0}

        def fake_uncached(query, limit=3):
            calls["n"] += 1
            return self._docs(1, 20)

        monkeypatch.setattr(lc, "_query_knowledge_base_uncached", fake_uncached)
        lc.query_knowledge_base("Contract law")
        lc.query_knowledge_base("Criminal law")
        assert calls["n"] == 2


# ── streaming transport ───────────────────────────────────────────────────

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


class TestStreaming:
    def test_stream_chat_parses_ndjson_and_sets_budget(self, monkeypatch):
        captured = {}

        def fake_post(url, json=None, stream=None, timeout=None):
            captured["url"] = url
            captured["json"] = json
            return _FakeStreamResponse([
                '{"message":{"content":"Hello"},"done":false}',
                "",
                '{"message":{"content":" world"},"done":false}',
                '{"message":{"content":""},"done":true}',
            ])

        monkeypatch.setattr(jstream.requests, "post", fake_post)
        chunks = list(jstream.stream_chat("hi", task_type="legal_flashcards"))
        assert "".join(chunks) == "Hello world"
        assert captured["url"].endswith("/api/chat")
        assert captured["json"]["stream"] is True
        assert captured["json"]["options"]["num_predict"] == \
            jprompt.budget_for("legal_flashcards")

    def test_stream_chat_raises_on_error_payload(self, monkeypatch):
        def fake_post(url, json=None, stream=None, timeout=None):
            return _FakeStreamResponse(['{"error":"model not found"}'])

        monkeypatch.setattr(jstream.requests, "post", fake_post)
        with pytest.raises(RuntimeError):
            list(jstream.stream_chat("hi"))


# ── bot integration ───────────────────────────────────────────────────────

class TestBotStreamingIntegration:
    @pytest.fixture(autouse=True)
    def _fixed_corpus_version(self, monkeypatch):
        from core.juris_kai import bot as jbot
        monkeypatch.setattr(jbot._cache, "corpus_version", lambda *a, **k: "docs=test")
        monkeypatch.setenv("JURIS_KAI_STREAM", "1")
        yield

    def _patch_telegram(self, monkeypatch, bot):
        calls = []

        def fake_api(method, data, timeout=35):
            calls.append((method, dict(data)))
            if method == "sendMessage":
                return {"ok": True, "result": {"message_id": 99}}
            return {"ok": True, "result": {}}

        monkeypatch.setattr(bot, "telegram_api", fake_api)
        return calls

    def test_generate_reply_streams_then_caches(self, monkeypatch):
        from core.juris_kai import bot as jbot
        calls = self._patch_telegram(monkeypatch, jbot)
        stream_calls = {"n": 0}

        def fake_stream(prompt, task_type="legal_research", **kw):
            stream_calls["n"] += 1
            yield "The "
            yield "answer."

        monkeypatch.setattr(jbot._streaming, "stream_chat", fake_stream)
        monkeypatch.setattr(jbot, "_delegate_with_timeout",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("blocking path must not run")))

        text, model, streamed, cached = jbot._generate_reply(
            "prompt", "juris_research", "What is contract law?",
            "contract law", "acct", chat_id=1, reply_markup="KB")
        assert text == "The answer." and streamed is True and cached is False
        assert stream_calls["n"] == 1
        methods = [c[0] for c in calls]
        assert "sendMessage" in methods and "editMessageText" in methods
        assert any(c[0] == "editMessageText" and c[1].get("reply_markup") == "KB"
                   for c in calls)

        # Second identical query is served from the generation cache.
        text2, _model2, streamed2, cached2 = jbot._generate_reply(
            "prompt", "juris_research", "what is CONTRACT law?!",
            "contract law", "acct", chat_id=1, reply_markup="KB")
        assert text2 == "The answer." and streamed2 is False and cached2 is True
        assert stream_calls["n"] == 1

    def test_generate_reply_falls_back_when_stream_fails(self, monkeypatch):
        from core.juris_kai import bot as jbot
        calls = self._patch_telegram(monkeypatch, jbot)

        def bad_stream(prompt, task_type="legal_research", **kw):
            raise RuntimeError("ollama down")
            yield  # pragma: no cover

        monkeypatch.setattr(jbot._streaming, "stream_chat", bad_stream)
        monkeypatch.setattr(jbot, "_delegate_with_timeout",
                            lambda *a, **k: ("blocked answer", "kai_brain"))

        text, model, streamed, cached = jbot._generate_reply(
            "prompt", "juris_research", "Query one", "q", "acct", chat_id=1)
        assert text == "blocked answer"
        assert model == "kai_brain"
        assert streamed is False and cached is False
        assert any(c[0] == "deleteMessage" for c in calls)

    def test_generate_reply_skips_stream_when_disabled(self, monkeypatch):
        from core.juris_kai import bot as jbot
        monkeypatch.setenv("JURIS_KAI_STREAM", "0")
        self._patch_telegram(monkeypatch, jbot)
        monkeypatch.setattr(jbot._streaming, "stream_chat",
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError("stream must not run")))
        monkeypatch.setattr(jbot, "_delegate_with_timeout",
                            lambda *a, **k: ("blocking only", "m"))
        text, model, streamed, cached = jbot._generate_reply(
            "prompt", "juris_research", "disabled query", "q", "acct", chat_id=1)
        assert text == "blocking only" and streamed is False and cached is False


# ── cache management API surface ──────────────────────────────────────────

class TestCacheManagement:
    def test_cache_stats_and_clear(self):
        jcache.GENERATION_CACHE.set(("x",), {"text": "hi"})
        jcache.RETRIEVAL_CACHE.set(("y",), [1, 2])
        stats = jcache.cache_stats()
        assert stats["generation"]["size"] == 1
        assert stats["retrieval"]["size"] == 1
        result = jcache.clear_caches()
        assert result["cleared"] == 2
        assert jcache.cache_stats()["generation"]["size"] == 0
