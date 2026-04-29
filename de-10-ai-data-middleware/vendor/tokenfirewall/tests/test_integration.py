from typing import Any

import pytest

from tokenfirewall import ask
from tokenfirewall.llm import LLMResponse, set_llm_client
from tokenfirewall.tokenizer import count_messages_tokens, count_text_tokens


class CountingClient:
    def __init__(self, answer: str = "llm answer", fail_first: bool = False) -> None:
        self.answer = answer
        self.fail_first = fail_first
        self.calls: list[list[dict[str, Any]]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_output_tokens: int = 300,
    ) -> LLMResponse:
        self.calls.append([dict(message) for message in messages])
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("first call failed")
        return LLMResponse(
            answer=self.answer,
            input_tokens=count_messages_tokens(messages, model),
            output_tokens=count_text_tokens(self.answer, model),
            latency_ms=1,
        )


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENFIREWALL_CACHE_PATH", str(tmp_path / "cache.sqlite3"))
    monkeypatch.setenv("TOKENFIREWALL_USAGE_PATH", str(tmp_path / "usage.sqlite3"))
    monkeypatch.setenv("TOKENFIREWALL_FORCE_MOCK", "1")
    monkeypatch.delenv("TOKENFIREWALL_DISABLE", raising=False)
    monkeypatch.delenv("TOKENFIREWALL_DAILY_TOKEN_BUDGET", raising=False)
    monkeypatch.delenv("TOKENFIREWALL_MONTHLY_TOKEN_BUDGET", raising=False)
    set_llm_client(None)
    yield
    set_llm_client(None)


def test_cache_hit_path_skips_llm() -> None:
    first_client = CountingClient(answer="cached answer")
    set_llm_client(first_client)
    first = ask("Who are you?", chat_history=[])
    assert first["cache_hit"] is False
    assert len(first_client.calls) == 1

    second_client = CountingClient(answer="should not be used")
    set_llm_client(second_client)
    second = ask("Who are you?", chat_history=[])

    assert second["answer"] == "cached answer"
    assert second["cache_hit"] is True
    assert len(second_client.calls) == 0


def test_tool_bypass_path_skips_llm() -> None:
    client = CountingClient()
    set_llm_client(client)

    result = ask("What is 234 * 98?", chat_history=[])

    assert result["answer"] == "22932"
    assert result["tool_used"] == "math"
    assert "tool_bypass" in result["strategy"]
    assert len(client.calls) == 0


def test_normal_path_calls_llm() -> None:
    client = CountingClient(answer="Paris")
    set_llm_client(client)

    result = ask("What is the capital of France?", chat_history=[])

    assert result["answer"] == "Paris"
    assert result["cache_hit"] is False
    assert result["tool_used"] is None
    assert len(client.calls) == 1


def test_llm_failure_falls_back_to_full_context_once() -> None:
    client = CountingClient(answer="fallback answer", fail_first=True)
    set_llm_client(client)
    chat_history = [
        {"role": "system", "content": "Stay helpful."},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "newer question"},
        {"role": "assistant", "content": "newer answer"},
    ]

    result = ask("Please answer this.", chat_history=chat_history, max_turns=1)

    assert result["answer"] == "fallback answer"
    assert "fallback_full_context" in result["strategy"]
    assert len(client.calls) == 2
    assert len(client.calls[0]) < len(client.calls[1])
    assert client.calls[1] == chat_history + [{"role": "user", "content": "Please answer this."}]


def test_debug_payload_contains_strategy_and_token_counts() -> None:
    client = CountingClient(answer="debug answer")
    set_llm_client(client)

    result = ask("Explain caching briefly.", chat_history=[], debug=True)

    assert result["debug"] is not None
    assert result["debug"]["strategy"] == result["strategy"]
    assert "token_counts" in result["debug"]
    assert result["debug"]["token_counts"]["baseline_input_tokens"] > 0
    assert "baseline_tokens" in result
    assert "optimized_tokens" in result
    assert "fallback_used" in result


def test_different_system_message_does_not_hit_cache() -> None:
    first_client = CountingClient(answer="first")
    set_llm_client(first_client)
    ask("Answer this.", chat_history=[{"role": "system", "content": "Style A"}])

    second_client = CountingClient(answer="second")
    set_llm_client(second_client)
    result = ask("Answer this.", chat_history=[{"role": "system", "content": "Style B"}])

    assert result["cache_hit"] is False
    assert len(second_client.calls) == 1


def test_different_notes_hash_does_not_hit_cache() -> None:
    first_client = CountingClient(answer="first")
    set_llm_client(first_client)
    ask("Answer this.", chat_history=[], notes="same notes", notes_hash="a")

    second_client = CountingClient(answer="second")
    set_llm_client(second_client)
    result = ask("Answer this.", chat_history=[], notes="same notes", notes_hash="b")

    assert result["cache_hit"] is False
    assert len(second_client.calls) == 1


def test_quality_check_failure_falls_back_to_full_context_once() -> None:
    client = CountingClient(answer="")

    def complete(messages, model, max_output_tokens=300):
        client.calls.append([dict(message) for message in messages])
        answer = "" if len(client.calls) == 1 else "full context answer"
        return LLMResponse(
            answer=answer,
            input_tokens=count_messages_tokens(messages, model),
            output_tokens=count_text_tokens(answer, model),
            latency_ms=1,
        )

    client.complete = complete
    set_llm_client(client)

    result = ask("Return a concise answer.", chat_history=[{"role": "user", "content": "old"}])

    assert result["answer"] == "full context answer"
    assert result["fallback_used"] is True
    assert "quality_check_failed" in result["strategy"]
    assert len(client.calls) == 2


def test_budget_blocking_skips_llm() -> None:
    client = CountingClient()
    set_llm_client(client)

    result = ask("Explain deployment risk in detail.", chat_history=[], daily_token_budget=1)

    assert result["budget_blocked"] is True
    assert "budget_blocked" in result["strategy"]
    assert len(client.calls) == 0


def test_force_override_allows_budget_exceeding_request() -> None:
    client = CountingClient(answer="forced")
    set_llm_client(client)

    result = ask(
        "Explain deployment risk in detail.",
        chat_history=[],
        daily_token_budget=1,
        force=True,
    )

    assert result["budget_blocked"] is False
    assert result["answer"] == "forced"
    assert len(client.calls) == 1


def test_mode_token_limits_are_passed_to_client() -> None:
    seen_limits: list[int] = []

    class LimitClient(CountingClient):
        def complete(self, messages, model, max_output_tokens=300):
            seen_limits.append(max_output_tokens)
            return super().complete(messages, model, max_output_tokens)

    set_llm_client(LimitClient())

    ask("Explain latency.", chat_history=[], mode="short")
    ask("Explain throughput.", chat_history=[], mode="normal")
    ask("Explain backpressure.", chat_history=[], mode="deep")

    assert seen_limits == [150, 400, 1200]


def test_long_context_guard_reduces_context() -> None:
    client = CountingClient(answer="guarded")
    set_llm_client(client)
    chat_history = [{"role": "system", "content": "Rules"}]
    for index in range(80):
        chat_history.append({"role": "user", "content": f"old message {index} " + "x " * 20})
        chat_history.append({"role": "assistant", "content": f"old answer {index} " + "y " * 20})

    result = ask(
        "What should I do next?",
        chat_history=chat_history,
        max_turns=1,
        long_context_threshold=200,
        debug=True,
    )

    assert result["debug"]["long_context_guard_used"] is True
    assert len(client.calls) == 1
    assert len(client.calls[0]) < len(chat_history)
    assert client.calls[0][0]["role"] == "system"


def test_usage_analytics_records_requests() -> None:
    from tokenfirewall.budget import usage_analytics

    client = CountingClient(answer="tracked")
    set_llm_client(client)
    ask("Track this request.", chat_history=[])
    ask("what is 2 + 2", chat_history=[])

    usage = usage_analytics()

    assert usage["total_requests"] == 2
    assert usage["llm_calls"] == 1
    assert usage["tool_bypasses"] == 1
    assert usage["total_estimated_tokens_saved"] >= 0
