import core.llm_clients as llm_clients


def test_pop_last_usage_returns_none_when_nothing_captured():
    assert llm_clients.pop_last_usage() is None


def test_call_ollama_qwen_captures_eval_counts(monkeypatch):
    class FakeOllamaResp:
        status_code = 200
        def json(self):
            return {
                "response": "local answer",
                "prompt_eval_count": 88,
                "eval_count": 21,
            }
        def raise_for_status(self):
            pass

    monkeypatch.setattr(llm_clients, "check_ollama_available", lambda: True)
    monkeypatch.setattr(llm_clients.requests, "post", lambda *a, **k: FakeOllamaResp())

    assert llm_clients.call_ollama_qwen("hello") == "local answer"
    assert llm_clients.pop_last_usage() == {"prompt_tokens": 88, "completion_tokens": 21}
