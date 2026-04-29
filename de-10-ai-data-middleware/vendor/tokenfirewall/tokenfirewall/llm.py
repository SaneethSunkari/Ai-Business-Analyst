"""Replaceable LLM client wrapper."""

from __future__ import annotations

import os
import re
import time
from importlib.util import find_spec
from dataclasses import dataclass
from typing import Any, Protocol

from .tokenizer import count_messages_tokens, count_text_tokens


@dataclass(frozen=True)
class LLMResponse:
    """Normalized LLM response."""

    answer: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMClient(Protocol):
    """Protocol for replaceable LLM clients."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_output_tokens: int = 300,
    ) -> LLMResponse:
        """Return a completion for structured messages."""


class MockLLMClient:
    """Deterministic test/local client that performs no network calls."""

    def __init__(self, answer: str | None = None) -> None:
        self.answer = answer
        self.calls: list[list[dict[str, Any]]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_output_tokens: int = 300,
    ) -> LLMResponse:
        start = time.perf_counter()
        self.calls.append([dict(message) for message in messages])
        last_user = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        answer = self.answer if self.answer is not None else self._mock_answer(last_user)
        output_tokens = min(count_text_tokens(answer, model), max_output_tokens)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return LLMResponse(
            answer=answer,
            input_tokens=count_messages_tokens(messages, model),
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

    def _mock_answer(self, prompt: str) -> str:
        """Return deterministic task-specific mock content."""

        if "Classify this DynamicAgentOS goal" in prompt:
            goal = _extract_after(prompt, "Goal:") or prompt
            lowered = goal.lower()
            if any(word in lowered for word in ("research", "evaluate", "investigate")):
                return "research"
            if any(word in lowered for word in ("build", "implement", "create")):
                return "build"
            if any(word in lowered for word in ("analyze", "compare", "review")):
                return "analysis"
            return "general"

        if "Create a concise numbered execution plan" in prompt:
            goal = _extract_after(prompt, "Goal:") or "the goal"
            return (
                f"1. Frame the useful decision behind: {goal}\n"
                "2. Gather focused evidence and constraints\n"
                "3. Synthesize risks, assumptions, and next actions"
            )

        if "Research the following topic" in prompt:
            topic = _extract_after(prompt, "Topic:") or "the topic"
            subtask = _extract_after(prompt, "Subtask:") or "the research question"
            return _research_mock(topic, subtask)

        if "Critique this DynamicAgentOS run" in prompt:
            return (
                "Summary: The run is structurally valid but should avoid any agent "
                "call that can be answered by memory, cache, or a deterministic tool.\n"
                "Risks:\n"
                "- Agent prompts may still be too generic.\n"
                "- Cost reports can look better on warm cache than fresh cache.\n"
                "Assumptions:\n"
                "- TokenFirewall metrics are trusted.\n"
                "- Mock mode is being used for local evaluation.\n"
                "Artifacts: critique_text\n"
                "Confidence: 0.72"
            )

        if "Verify this DynamicAgentOS answer" in prompt:
            return (
                "Summary: Verification completed through the mock verifier.\n"
                "Risks:\n"
                "- Deterministic checks may miss semantic errors.\n"
                "- The answer may be structurally valid but shallow.\n"
                "Assumptions:\n"
                "- Required fields were visible in the answer.\n"
                "- Cost trace fields were present.\n"
                "Artifacts: verification_text\n"
                "Confidence: 0.70"
            )

        return (
            "Summary: Mock response generated for the requested task.\n"
            "Findings:\n"
            "- The prompt was processed in local mock mode.\n"
            "- No network model call was made.\n"
            "- The output is deterministic for repeatability.\n"
            "Risks:\n"
            "- Mock content is not a substitute for real analysis.\n"
            "- Prompt-specific nuance may be limited.\n"
            "Assumptions:\n"
            "- Local testing is the goal.\n"
            "- The caller needs stable output.\n"
            "Next actions:\n"
            "- Inspect the result.\n"
            "- Run tests.\n"
            "- Replace mock mode for real evaluation.\n"
            "Artifacts: mock_text\n"
            "Confidence: 0.60"
        )


class OpenAIChatClient:
    """Optional OpenAI SDK adapter loaded only when used."""

    def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_output_tokens: int = 300,
    ) -> LLMResponse:
        start = time.perf_counter()
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("openai SDK is not installed") from exc

        client = OpenAI()
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": str(message["role"]), "content": str(message["content"])}
                for message in messages
            ],
            max_tokens=max_output_tokens,
        )
        answer = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        input_tokens = (
            int(getattr(usage, "prompt_tokens", 0))
            if usage is not None
            else count_messages_tokens(messages, model)
        )
        output_tokens = (
            int(getattr(usage, "completion_tokens", 0))
            if usage is not None
            else count_text_tokens(answer, model)
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        return LLMResponse(answer, input_tokens, output_tokens, latency_ms)


_CLIENT: LLMClient | None = None


def _extract_after(prompt: str, label: str) -> str | None:
    pattern = re.compile(rf"(?im)^\s*{re.escape(label)}\s*(.+)$")
    match = pattern.search(prompt)
    if not match:
        return None
    return match.group(1).strip()


def _research_mock(topic: str, subtask: str) -> str:
    focus = subtask.rstrip(".")
    return (
        f"Summary: {topic} is useful as a personal AI workbench only if it turns "
        f"{focus.lower()} into repeatable, inspectable workflows instead of loose chat.\n"
        "Findings:\n"
        "- The current orchestration gives the project a clear control plane for goals, planning, execution, review, and cost reporting. [citation: mock-architecture]\n"
        "- TokenFirewall integration is the strongest practical foundation because every model-facing call can be cached, routed, budgeted, or bypassed. [citation: tokenfirewall-metrics]\n"
        "- The workbench still needs stronger task artifacts and persistent memory before it can reliably build complex ideas end to end. [citation: mock-gap-analysis]\n"
        "Risks:\n"
        "- Generic agent prompts can create confident but shallow reports.\n"
        "- Warm-cache savings may hide the true cost of first-run exploration.\n"
        "Assumptions:\n"
        "- The user wants a local-first personal workbench rather than an autonomous external-action agent.\n"
        "- Mock mode should produce useful structure without pretending to be fresh external research.\n"
        "Next actions:\n"
        "- Make one workflow excellent, such as idea evaluation to build plan.\n"
        "- Persist memory and artifacts in a local database.\n"
        "- Add deterministic checks before costly review steps.\n"
        "Artifacts: structured_mock_research\n"
        "Confidence: 0.76"
    )


def set_llm_client(client: LLMClient | None) -> None:
    """Set the process-local LLM client used by ``call_llm``."""

    global _CLIENT
    _CLIENT = client


def get_llm_client() -> LLMClient:
    """Return the configured client, falling back to optional OpenAI or mock."""

    if _CLIENT is not None:
        return _CLIENT
    if (
        os.environ.get("OPENAI_API_KEY")
        and os.environ.get("TOKENFIREWALL_FORCE_MOCK") != "1"
        and find_spec("openai") is not None
    ):
        return OpenAIChatClient()
    return MockLLMClient()


def call_llm(
    messages: list[dict[str, Any]],
    model: str,
    max_output_tokens: int = 300,
) -> tuple[str, int, int, int]:
    """Call the configured LLM client and return answer and token usage."""

    result = get_llm_client().complete(messages, model, max_output_tokens)
    return result.answer, result.input_tokens, result.output_tokens, result.latency_ms
