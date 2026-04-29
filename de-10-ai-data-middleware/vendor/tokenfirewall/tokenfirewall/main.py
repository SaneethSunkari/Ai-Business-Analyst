"""TokenFirewall public API and CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .budget import (
    budget_status,
    check_budget,
    estimate_cost_usd,
    record_usage_event,
    usage_analytics,
)
from .cache import cache_get, cache_set, make_key, normalize_text
from .llm import call_llm
from .metrics import calculate_savings
from .pruner import prune_context
from .router import route_query, router_config_from_env
from .tokenizer import count_messages_tokens, count_text_tokens
from .tools import run_tools, tools_version

FIREWALL_VERSION = "0.1.1"
MODE_OUTPUT_TOKENS = {"short": 150, "normal": 400, "deep": 1200}
DEFAULT_LONG_CONTEXT_THRESHOLD = 3000


def _validate_message(message: dict[str, Any], index: int) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise TypeError(f"chat_history[{index}] must be a dict")
    role = message.get("role")
    content = message.get("content")
    if not isinstance(role, str) or not role.strip():
        raise ValueError(f"chat_history[{index}].role must be a non-empty string")
    if not isinstance(content, str):
        raise ValueError(f"chat_history[{index}].content must be a string")
    cleaned = dict(message)
    cleaned["role"] = role.strip()
    cleaned["content"] = content
    return cleaned


def _validate_inputs(
    query: str,
    chat_history: list[dict[str, Any]],
    notes: str | None,
    model: str | None,
    max_turns: int,
    max_output_tokens: int,
    mode: str,
) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(chat_history, list):
        raise TypeError("chat_history must be a list of dicts")
    if notes is not None and not isinstance(notes, str):
        raise TypeError("notes must be a string or None")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("model must be a non-empty string when provided")
    if not isinstance(max_turns, int) or max_turns < 1:
        raise ValueError("max_turns must be an integer >= 1")
    if not isinstance(max_output_tokens, int) or max_output_tokens < 1:
        raise ValueError("max_output_tokens must be an integer >= 1")
    if mode not in MODE_OUTPUT_TOKENS:
        raise ValueError("mode must be one of: short, normal, deep")


def _effective_notes_hash(notes: str | None, notes_hash: str | None) -> str | None:
    if notes_hash:
        return notes_hash
    if notes is None:
        return None
    return hashlib.sha256(normalize_text(notes).encode("utf-8")).hexdigest()


def _build_messages(
    query: str,
    chat_history: list[dict[str, Any]],
    notes: str | None,
) -> list[dict[str, Any]]:
    messages = [_validate_message(message, index) for index, message in enumerate(chat_history)]
    if notes:
        insert_at = 0
        while insert_at < len(messages) and messages[insert_at].get("role") == "system":
            insert_at += 1
        messages.insert(insert_at, {"role": "system", "content": f"Notes:\n{notes}"})
    messages.append({"role": "user", "content": query})
    return messages


def _latency_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _resolve_max_output_tokens(mode: str, max_output_tokens: int | None) -> int:
    if max_output_tokens is not None:
        return max_output_tokens
    return MODE_OUTPUT_TOKENS[mode]


def _keyword_set(text: str) -> set[str]:
    return {
        token
        for token in (
            "".join(ch.lower() if ch.isalnum() else " " for ch in text).split()
        )
        if len(token) >= 4
    }


def _notes_relevant(query: str, notes: str | None) -> bool:
    if not notes:
        return False
    query_keywords = _keyword_set(query)
    notes_keywords = _keyword_set(notes)
    return bool(query_keywords & notes_keywords)


def _remove_notes_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        message
        for message in messages
        if not (
            message.get("role") == "system"
            and isinstance(message.get("content"), str)
            and str(message.get("content", "")).startswith("Notes:\n")
        )
    ]


def _apply_long_context_guard(
    *,
    messages: list[dict[str, Any]],
    query: str,
    notes: str | None,
    model: str,
    max_turns: int,
    threshold: int,
) -> tuple[list[dict[str, Any]], bool, bool]:
    """Reduce long context before any LLM call."""

    if threshold <= 0 or count_messages_tokens(messages, model) <= threshold:
        return messages, False, bool(notes)
    guarded_source = messages if _notes_relevant(query, notes) else _remove_notes_messages(messages)
    guarded = prune_context(guarded_source, max_turns=max_turns, important_tags={"durable_fact"})
    return guarded, True, guarded_source is messages and bool(notes)


def _quality_check_answer(answer: str, query: str) -> tuple[bool, str | None]:
    """Run deterministic response checks before accepting a pruned LLM answer."""

    stripped = answer.strip()
    lowered_query = query.lower()
    if not stripped:
        return False, "empty_answer"
    if stripped.endswith(("...", "…")) or stripped.count("```") % 2:
        return False, "obviously_truncated"
    if "json" in lowered_query:
        if not (
            (stripped.startswith("{") and stripped.endswith("}"))
            or (stripped.startswith("[") and stripped.endswith("]"))
        ):
            return False, "expected_json"
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            return False, "invalid_json"
    if any(marker in lowered_query for marker in ("list", "bullet", "bullets")):
        has_list_shape = (
            "\n-" in stripped
            or "\n*" in stripped
            or any(line.lstrip()[:2] in {"1.", "2.", "3."} for line in stripped.splitlines())
            or (stripped.startswith("[") and stripped.endswith("]"))
        )
        if not has_list_shape:
            return False, "expected_list"
    code_markers = (
        "write code",
        "provide code",
        "python code",
        "write a python",
        "write a function",
        "function that",
        "script",
    )
    if any(marker in lowered_query for marker in code_markers):
        has_code_shape = "```" in stripped or "def " in stripped or "class " in stripped
        if not has_code_shape:
            return False, "expected_code"
    return True, None


def _result(
    *,
    answer: str,
    strategy: list[str],
    input_tokens: int,
    output_tokens: int,
    baseline_input_tokens: int,
    baseline_output_tokens: int,
    optimized_input_tokens: int,
    optimized_output_tokens: int,
    cache_hit: bool,
    tool_used: str | None,
    latency_ms: int,
    fallback_used: bool,
    selected_model: str,
    estimated_cost_usd: float,
    estimated_cost_saved_usd: float,
    daily_tokens_used: int,
    monthly_tokens_used: int,
    budget_blocked: bool,
    debug: bool,
    debug_payload: dict[str, Any],
) -> dict[str, Any]:
    savings = calculate_savings(
        baseline_input_tokens,
        baseline_output_tokens,
        optimized_input_tokens,
        optimized_output_tokens,
    )
    debug_payload["strategy"] = list(strategy)
    debug_payload["token_counts"] = {
        "baseline_input_tokens": baseline_input_tokens,
        "baseline_output_tokens": baseline_output_tokens,
        "optimized_input_tokens": optimized_input_tokens,
        "optimized_output_tokens": optimized_output_tokens,
    }
    return {
        "answer": answer,
        "strategy": list(strategy),
        "baseline_tokens": int(savings["baseline_total"]),
        "optimized_tokens": int(savings["optimized_total"]),
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "saved_tokens": int(savings["saved_tokens"]),
        "saved_percent": float(savings["saved_percent"]),
        "cache_hit": cache_hit,
        "tool_used": tool_used,
        "latency_ms": int(latency_ms),
        "fallback_used": fallback_used,
        "selected_model": selected_model,
        "estimated_cost_usd": float(estimated_cost_usd),
        "estimated_cost_saved_usd": float(estimated_cost_saved_usd),
        "daily_tokens_used": int(daily_tokens_used),
        "monthly_tokens_used": int(monthly_tokens_used),
        "budget_blocked": budget_blocked,
        "debug": debug_payload if debug else None,
    }


def _record_result_usage(result: dict[str, Any], request_id: str) -> None:
    """Persist usage analytics without letting telemetry break requests."""

    try:
        record_usage_event(
            {
                "request_id": request_id,
                "selected_model": result["selected_model"],
                "strategy": result["strategy"],
                "cache_hit": result["cache_hit"],
                "tool_used": result["tool_used"],
                "llm_call": (
                    not result["cache_hit"]
                    and result["tool_used"] is None
                    and not result["budget_blocked"]
                    and "llm" in result["strategy"]
                ),
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "baseline_tokens": result["baseline_tokens"],
                "optimized_tokens": result["optimized_tokens"],
                "saved_tokens": result["saved_tokens"],
                "estimated_cost_usd": result["estimated_cost_usd"],
                "estimated_cost_saved_usd": result["estimated_cost_saved_usd"],
                "budget_blocked": result["budget_blocked"],
            }
        )
    except Exception:
        return


def ask(
    query: str,
    chat_history: list[dict[str, Any]],
    notes: str | None = None,
    model: str | None = None,
    notes_hash: str | None = None,
    max_turns: int = 5,
    max_output_tokens: int | None = None,
    mode: str = "normal",
    debug: bool = False,
    disable: bool = False,
    force: bool = False,
    max_cost: float | None = None,
    daily_token_budget: int | None = None,
    monthly_token_budget: int | None = None,
    long_context_threshold: int = DEFAULT_LONG_CONTEXT_THRESHOLD,
) -> dict[str, Any]:
    """Run the TokenFirewall decision layer before an LLM call."""

    start = time.perf_counter()
    request_id = str(uuid.uuid4())
    strategy: list[str] = []
    resolved_max_output_tokens = _resolve_max_output_tokens(mode, max_output_tokens)
    model_override = model.strip() if isinstance(model, str) and model.strip() else None
    router_config = router_config_from_env()
    routing = route_query(query, model_override=model_override, config=router_config)
    selected_model = routing.selected_model
    baseline_model = model_override or router_config.default_model
    token_model = selected_model if selected_model != "tool" else baseline_model
    status = budget_status()
    daily_tokens_used = int(status["daily_tokens_used"] or 0)
    monthly_tokens_used = int(status["monthly_tokens_used"] or 0)

    def finish(
        *,
        answer: str,
        input_tokens: int,
        output_tokens: int,
        baseline_input_tokens: int,
        baseline_output_tokens: int,
        optimized_input_tokens: int,
        optimized_output_tokens: int,
        cache_hit: bool,
        tool_used: str | None,
        fallback_used: bool,
        estimated_cost_usd: float,
        budget_blocked: bool,
        debug_payload: dict[str, Any],
    ) -> dict[str, Any]:
        baseline_cost = estimate_cost_usd(
            baseline_model,
            baseline_input_tokens,
            baseline_output_tokens,
        )
        estimated_cost_saved_usd = max(0.0, baseline_cost - estimated_cost_usd)
        paid_tokens = (
            0
            if budget_blocked or cache_hit or tool_used
            else optimized_input_tokens + optimized_output_tokens
        )
        result = _result(
            answer=answer,
            strategy=strategy,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            baseline_input_tokens=baseline_input_tokens,
            baseline_output_tokens=baseline_output_tokens,
            optimized_input_tokens=optimized_input_tokens,
            optimized_output_tokens=optimized_output_tokens,
            cache_hit=cache_hit,
            tool_used=tool_used,
            latency_ms=_latency_ms(start),
            fallback_used=fallback_used,
            selected_model=selected_model,
            estimated_cost_usd=estimated_cost_usd,
            estimated_cost_saved_usd=estimated_cost_saved_usd,
            daily_tokens_used=daily_tokens_used + paid_tokens,
            monthly_tokens_used=monthly_tokens_used + paid_tokens,
            budget_blocked=budget_blocked,
            debug=debug,
            debug_payload=debug_payload,
        )
        _record_result_usage(result, request_id)
        return result

    _validate_inputs(
        query,
        chat_history,
        notes,
        model_override,
        max_turns,
        resolved_max_output_tokens,
        mode,
    )
    messages = _build_messages(query, chat_history, notes)
    strategy.append("validated")
    strategy.append("messages_built")
    strategy.append(f"route:{routing.reason}")

    baseline_input_tokens = count_messages_tokens(messages, baseline_model)
    strategy.append("baseline_counted")

    effective_notes_hash = _effective_notes_hash(notes, notes_hash)
    current_tools_version = tools_version()
    cache_key = make_key(
        messages,
        model=selected_model,
        notes_hash=effective_notes_hash,
        version=FIREWALL_VERSION,
        tools_version=current_tools_version,
        mode=mode,
    )
    debug_payload: dict[str, Any] = {
        "request_id": request_id,
        "model": selected_model,
        "selected_model": selected_model,
        "baseline_model": baseline_model,
        "routing_reason": routing.reason,
        "mode": mode,
        "max_output_tokens": resolved_max_output_tokens,
        "firewall_version": FIREWALL_VERSION,
        "tools_version": current_tools_version,
        "cache_key": cache_key,
        "notes_hash": effective_notes_hash,
        "message_counts": {"full": len(messages)},
        "long_context_guard_used": False,
    }

    if disable or os.environ.get("TOKENFIREWALL_DISABLE") == "1":
        strategy.append("disabled")
        estimated_input_tokens = count_messages_tokens(messages, baseline_model)
        preflight_cost = estimate_cost_usd(
            baseline_model,
            estimated_input_tokens,
            resolved_max_output_tokens,
        )
        allowed, block_reason, budget_snapshot = check_budget(
            estimated_tokens=estimated_input_tokens + resolved_max_output_tokens,
            estimated_cost_usd=preflight_cost,
            force=force,
            max_cost=max_cost,
            daily_token_budget=daily_token_budget,
            monthly_token_budget=monthly_token_budget,
        )
        debug_payload["budget"] = budget_snapshot
        debug_payload["preflight_estimated_cost_usd"] = preflight_cost
        if not allowed:
            strategy.append("budget_blocked")
            debug_payload["budget_block_reason"] = block_reason
            return finish(
                answer=f"TokenFirewall blocked this request: {block_reason}. Use force=True to override.",
                input_tokens=0,
                output_tokens=0,
                baseline_input_tokens=baseline_input_tokens,
                baseline_output_tokens=resolved_max_output_tokens,
                optimized_input_tokens=0,
                optimized_output_tokens=0,
                cache_hit=False,
                tool_used=None,
                fallback_used=False,
                estimated_cost_usd=0.0,
                budget_blocked=True,
                debug_payload=debug_payload,
            )
        strategy.append("budget_force_override" if force else "budget_checked")
        answer, input_tokens, output_tokens, _ = call_llm(
            messages,
            baseline_model,
            max_output_tokens=resolved_max_output_tokens,
        )
        strategy.append("llm")
        return finish(
            answer=answer,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            baseline_input_tokens=baseline_input_tokens,
            baseline_output_tokens=output_tokens,
            optimized_input_tokens=input_tokens,
            optimized_output_tokens=output_tokens,
            cache_hit=False,
            tool_used=None,
            fallback_used=False,
            estimated_cost_usd=estimate_cost_usd(baseline_model, input_tokens, output_tokens),
            budget_blocked=False,
            debug_payload=debug_payload,
        )

    try:
        cached = cache_get(cache_key)
    except Exception as exc:
        cached = None
        strategy.append("cache_error")
        debug_payload["cache_error"] = repr(exc)

    if cached is not None:
        strategy.append("cache_hit")
        return finish(
            answer=str(cached["response"]),
            input_tokens=0,
            output_tokens=0,
            baseline_input_tokens=baseline_input_tokens,
            baseline_output_tokens=int(cached["output_tokens"]),
            optimized_input_tokens=0,
            optimized_output_tokens=0,
            cache_hit=True,
            tool_used=None,
            fallback_used=False,
            estimated_cost_usd=0.0,
            budget_blocked=False,
            debug_payload={
                **debug_payload,
                "cached_token_counts": {
                    "input_tokens": int(cached["input_tokens"]),
                    "output_tokens": int(cached["output_tokens"]),
                },
                "cached_strategy": cached["strategy"],
            },
        )

    strategy.append("cache_miss")

    tool_result = run_tools(query)
    if tool_result is not None:
        tool_name = str(tool_result.get("name"))
        strategy.append("tool_bypass")
        answer = str(tool_result.get("answer") or tool_result.get("error") or "")
        baseline_output_tokens = count_text_tokens(answer, baseline_model)
        return finish(
            answer=answer,
            input_tokens=0,
            output_tokens=0,
            baseline_input_tokens=baseline_input_tokens,
            baseline_output_tokens=baseline_output_tokens,
            optimized_input_tokens=0,
            optimized_output_tokens=0,
            cache_hit=False,
            tool_used=tool_name,
            fallback_used=False,
            estimated_cost_usd=0.0,
            budget_blocked=False,
            debug_payload={**debug_payload, "tool_result": tool_result},
        )

    strategy.append("tools_skipped")
    if selected_model == "tool":
        selected_model = baseline_model
        token_model = selected_model
        debug_payload["selected_model"] = selected_model
        debug_payload["model"] = selected_model

    guarded_source, long_context_guard_used, notes_kept = _apply_long_context_guard(
        messages=messages,
        query=query,
        notes=notes,
        model=token_model,
        max_turns=max_turns,
        threshold=long_context_threshold,
    )
    if long_context_guard_used:
        strategy.append("long_context_guard")
    pruned_messages = (
        guarded_source
        if long_context_guard_used
        else prune_context(messages, max_turns=max_turns)
    )
    fallback_messages = guarded_source if long_context_guard_used else messages
    strategy.append("context_pruned")
    debug_payload["long_context_guard_used"] = long_context_guard_used
    debug_payload["notes_kept_after_long_context_guard"] = notes_kept
    debug_payload["message_counts"]["pruned"] = len(pruned_messages)
    estimated_input_tokens = count_messages_tokens(pruned_messages, token_model)
    debug_payload["pruned_input_tokens_estimate"] = estimated_input_tokens
    preflight_cost = estimate_cost_usd(
        selected_model,
        estimated_input_tokens,
        resolved_max_output_tokens,
    )
    allowed, block_reason, budget_snapshot = check_budget(
        estimated_tokens=estimated_input_tokens + resolved_max_output_tokens,
        estimated_cost_usd=preflight_cost,
        force=force,
        max_cost=max_cost,
        daily_token_budget=daily_token_budget,
        monthly_token_budget=monthly_token_budget,
    )
    debug_payload["budget"] = budget_snapshot
    debug_payload["preflight_estimated_cost_usd"] = preflight_cost
    if not allowed:
        strategy.append("budget_blocked")
        debug_payload["budget_block_reason"] = block_reason
        return finish(
            answer=f"TokenFirewall blocked this request: {block_reason}. Use force=True to override.",
            input_tokens=0,
            output_tokens=0,
            baseline_input_tokens=baseline_input_tokens,
            baseline_output_tokens=resolved_max_output_tokens,
            optimized_input_tokens=0,
            optimized_output_tokens=0,
            cache_hit=False,
            tool_used=None,
            fallback_used=False,
            estimated_cost_usd=0.0,
            budget_blocked=True,
            debug_payload=debug_payload,
        )
    strategy.append("budget_force_override" if force else "budget_checked")

    fallback_used = False

    def fallback_with_protected_context() -> tuple[str, int, int]:
        fallback_answer, fallback_input_tokens, fallback_output_tokens, _ = call_llm(
            fallback_messages,
            selected_model,
            max_output_tokens=resolved_max_output_tokens,
        )
        strategy.append("llm")
        return fallback_answer, fallback_input_tokens, fallback_output_tokens

    try:
        answer, input_tokens, output_tokens, _ = call_llm(
            pruned_messages,
            selected_model,
            max_output_tokens=resolved_max_output_tokens,
        )
        strategy.append("llm")
    except Exception as exc:
        fallback_used = True
        strategy.append("llm_error")
        strategy.append("fallback_full_context")
        debug_payload["first_llm_error"] = repr(exc)
        try:
            answer, input_tokens, output_tokens = fallback_with_protected_context()
        except Exception as fallback_exc:
            strategy.append("fallback_failed")
            debug_payload["fallback_llm_error"] = repr(fallback_exc)
            return finish(
                answer="TokenFirewall could not complete the request after retrying with protected context.",
                input_tokens=estimated_input_tokens,
                output_tokens=0,
                baseline_input_tokens=baseline_input_tokens,
                baseline_output_tokens=0,
                optimized_input_tokens=estimated_input_tokens,
                optimized_output_tokens=0,
                cache_hit=False,
                tool_used=None,
                fallback_used=True,
                estimated_cost_usd=0.0,
                budget_blocked=False,
                debug_payload=debug_payload,
            )
    else:
        quality_passed, quality_reason = _quality_check_answer(answer, query)
        if not quality_passed:
            fallback_used = True
            strategy.append("quality_check_failed")
            strategy.append("fallback_full_context")
            debug_payload["quality_check_error"] = quality_reason
            try:
                answer, input_tokens, output_tokens = fallback_with_protected_context()
            except Exception as fallback_exc:
                strategy.append("fallback_failed")
                debug_payload["fallback_llm_error"] = repr(fallback_exc)
                return finish(
                    answer="TokenFirewall could not complete the request after retrying with protected context.",
                    input_tokens=estimated_input_tokens,
                    output_tokens=0,
                    baseline_input_tokens=baseline_input_tokens,
                    baseline_output_tokens=0,
                    optimized_input_tokens=estimated_input_tokens,
                    optimized_output_tokens=0,
                    cache_hit=False,
                    tool_used=None,
                    fallback_used=True,
                    estimated_cost_usd=0.0,
                    budget_blocked=False,
                    debug_payload=debug_payload,
                )

    try:
        cache_set(
            cache_key,
            response=answer,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            strategy=strategy,
            model=selected_model,
        )
        strategy.append("cached")
    except Exception as exc:
        strategy.append("cache_set_error")
        debug_payload["cache_set_error"] = repr(exc)

    return finish(
        answer=answer,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        baseline_input_tokens=baseline_input_tokens,
        baseline_output_tokens=output_tokens,
        optimized_input_tokens=input_tokens,
        optimized_output_tokens=output_tokens,
        cache_hit=False,
        tool_used=None,
        fallback_used=fallback_used,
        estimated_cost_usd=estimate_cost_usd(selected_model, input_tokens, output_tokens),
        budget_blocked=False,
        debug_payload=debug_payload,
    )


def _load_chat(path: str | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        data = data["messages"]
    if not isinstance(data, list):
        raise ValueError("chat JSON must be a list or an object with a messages list")
    return data


def _load_notes(path: str | None) -> str | None:
    if path is None:
        return None
    return Path(path).read_text(encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tokenfirewall")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ask_parser = subparsers.add_parser("ask", help="ask through TokenFirewall")
    ask_parser.add_argument("query", nargs="?")
    ask_parser.add_argument("--chat", help="path to chat JSON")
    ask_parser.add_argument("--notes", help="path to notes text file")
    ask_parser.add_argument("--model")
    ask_parser.add_argument("--mode", choices=sorted(MODE_OUTPUT_TOKENS), default="normal")
    ask_parser.add_argument("--max-turns", type=int, default=5)
    ask_parser.add_argument("--max-output-tokens", type=int)
    ask_parser.add_argument("--budget-status", action="store_true")
    ask_parser.add_argument("--force", action="store_true")
    ask_parser.add_argument("--max-cost", type=float)
    ask_parser.add_argument("--debug", action="store_true")
    ask_parser.add_argument("--disable", action="store_true")

    subparsers.add_parser("usage", help="show TokenFirewall usage analytics")

    server_parser = subparsers.add_parser("server", help="run local HTTP gateway")
    server_parser.add_argument("--host", default="127.0.0.1")
    server_parser.add_argument("--port", type=int, default=8787)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ask":
            if args.budget_status:
                print(json.dumps(budget_status(), indent=2, sort_keys=True))
                return 0
            if not args.query:
                parser.error("ask requires a query unless --budget-status is used")
            result = ask(
                args.query,
                chat_history=_load_chat(args.chat),
                notes=_load_notes(args.notes),
                model=args.model,
                max_turns=args.max_turns,
                max_output_tokens=args.max_output_tokens,
                mode=args.mode,
                debug=args.debug,
                disable=args.disable,
                force=args.force,
                max_cost=args.max_cost,
            )
            print(result["answer"])
            print(
                "metrics: "
                f"strategy={','.join(result['strategy'])} "
                f"selected_model={result['selected_model']} "
                f"cache_hit={result['cache_hit']} "
                f"tool_used={result['tool_used']} "
                f"input_tokens={result['input_tokens']} "
                f"output_tokens={result['output_tokens']} "
                f"baseline_tokens={result['baseline_tokens']} "
                f"optimized_tokens={result['optimized_tokens']} "
                f"saved_tokens={result['saved_tokens']} "
                f"saved_percent={result['saved_percent']:.2f}% "
                f"estimated_cost_usd={result['estimated_cost_usd']:.6f} "
                f"daily_tokens_used={result['daily_tokens_used']} "
                f"monthly_tokens_used={result['monthly_tokens_used']} "
                f"budget_blocked={result['budget_blocked']} "
                f"fallback_used={result['fallback_used']} "
                f"latency_ms={result['latency_ms']}"
            )
            if args.debug:
                print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "usage":
            print(json.dumps(usage_analytics(), indent=2, sort_keys=True))
            return 0
        if args.command == "server":
            from .server import serve

            serve(host=args.host, port=args.port)
            return 0
    except Exception as exc:
        print(f"tokenfirewall: unexpected error: {exc}", file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 2
