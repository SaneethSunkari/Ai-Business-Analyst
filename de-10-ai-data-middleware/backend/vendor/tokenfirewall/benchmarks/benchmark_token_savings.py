"""Benchmark TokenFirewall savings against a direct full-context baseline."""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tokenfirewall import ask  # noqa: E402
from tokenfirewall.budget import estimate_cost_usd  # noqa: E402
from tokenfirewall.llm import LLMResponse, set_llm_client  # noqa: E402
from tokenfirewall.router import router_config_from_env  # noqa: E402
from tokenfirewall.tokenizer import count_messages_tokens, count_text_tokens  # noqa: E402

MIN_SAVED_PERCENT = 60.0
MAX_P90_OVERHEAD_MS = 150.0
MIN_QUALITY_PASS_RATE = 0.98


class BenchmarkLLMClient:
    """Deterministic benchmark client with realistic-shaped answers."""

    def __init__(self, answers_by_query: dict[str, str]) -> None:
        self.answers_by_query = answers_by_query
        self.calls = 0

    def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_output_tokens: int = 300,
    ) -> LLMResponse:
        start = time.perf_counter()
        self.calls += 1
        query = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        answer = self.answers_by_query.get(query, f"Benchmark answer: {query}")
        return LLMResponse(
            answer=answer,
            input_tokens=count_messages_tokens(messages, model),
            output_tokens=min(count_text_tokens(answer, model), max_output_tokens),
            latency_ms=int((time.perf_counter() - start) * 1000),
        )


def _load_workload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    if not isinstance(cases, list) or len(cases) < 100:
        raise ValueError("benchmark_queries.json must contain at least 100 cases")
    return payload


def _expand_case(case: dict[str, Any], workload: dict[str, Any]) -> dict[str, Any]:
    histories = workload.get("histories", {})
    notes_templates = workload.get("notes", {})
    expanded = dict(case)
    history_template = expanded.pop("history_template", None)
    notes_template = expanded.pop("notes_template", None)
    expanded["chat_history"] = (
        histories.get(history_template, []) if history_template else expanded.get("chat_history", [])
    )
    expanded["notes"] = notes_templates.get(notes_template) if notes_template else expanded.get("notes")
    return expanded


def _prepare_allowed_files(workload: dict[str, Any], directory: Path) -> None:
    for name, content in workload.get("files", {}).items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")


def _build_messages(case: dict[str, Any]) -> list[dict[str, Any]]:
    messages = [dict(message) for message in case.get("chat_history", [])]
    notes = case.get("notes")
    if notes:
        insert_at = 0
        while insert_at < len(messages) and messages[insert_at].get("role") == "system":
            insert_at += 1
        messages.insert(insert_at, {"role": "system", "content": f"Notes:\n{notes}"})
    messages.append({"role": "user", "content": str(case["query"])})
    return messages


def _percent(numerator: int, denominator: int) -> float:
    return (numerator / denominator * 100.0) if denominator else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return (numerator / denominator) if denominator else 0.0


def _p50(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _p90(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(0.9 * (len(ordered) - 1))
    return float(ordered[index])


def _quality_passed(case: dict[str, Any], answer: str) -> bool:
    expected = case.get("expected_contains")
    if expected is None:
        return bool(answer.strip())
    if isinstance(expected, list):
        return all(str(item) in answer for item in expected)
    return str(expected) in answer


def _print_table(summary: dict[str, Any]) -> None:
    rows = [
        ("cases", summary["cases"]),
        ("total_baseline_tokens", summary["total_baseline_tokens"]),
        ("total_optimized_tokens", summary["total_optimized_tokens"]),
        ("saved_tokens", summary["saved_tokens"]),
        ("saved_percent", f"{summary['saved_percent']:.2f}%"),
        ("previous_saved_percent", f"{summary['previous_saved_percent']:.2f}%"),
        ("cost_saved_percent", f"{summary['cost_saved_percent']:.2f}%"),
        ("additional_cost_saved_percent", f"{summary['additional_cost_saved_percent']:.2f}%"),
        ("cache_hit_rate", f"{summary['cache_hit_rate'] * 100.0:.2f}%"),
        ("tool_bypass_rate", f"{summary['tool_bypass_rate'] * 100.0:.2f}%"),
        ("budget_blocks", summary["budget_blocks"]),
        ("avg_selected_model_tier", f"{summary['avg_selected_model_tier']:.2f}"),
        ("avg_latency_ms", f"{summary['avg_latency_ms']:.2f}"),
        ("p50_latency_ms", f"{summary['p50_latency_ms']:.2f}"),
        ("p90_latency_ms", f"{summary['p90_latency_ms']:.2f}"),
        ("p90_latency_overhead_ms", f"{summary['p90_latency_overhead_ms']:.2f}"),
        ("quality_check_pass_rate", f"{summary['quality_check_pass_rate'] * 100.0:.2f}%"),
    ]
    width = max(len(name) for name, _ in rows)
    print("TokenFirewall Benchmark")
    print("-" * (width + 18))
    for name, value in rows:
        print(f"{name:<{width}}  {value}")
    print("-" * (width + 18))


def run_benchmark() -> dict[str, Any]:
    workload = _load_workload(Path(__file__).with_name("benchmark_queries.json"))
    cases = [_expand_case(case, workload) for case in workload["cases"]]
    answers = {
        str(case["query"]): str(case.get("answer", f"Benchmark answer: {case['query']}"))
        for case in cases
    }

    with tempfile.TemporaryDirectory(prefix="tokenfirewall-bench-") as tmp:
        temp_dir = Path(tmp)
        cache_path = temp_dir / "cache.sqlite3"
        allowed_dir = temp_dir / "allowed"
        allowed_dir.mkdir()
        _prepare_allowed_files(workload, allowed_dir)

        os.environ["TOKENFIREWALL_CACHE_PATH"] = str(cache_path)
        os.environ["TOKENFIREWALL_USAGE_PATH"] = str(temp_dir / "usage.sqlite3")
        os.environ["TOKENFIREWALL_ALLOWED_DIR"] = str(allowed_dir)
        os.environ["TOKENFIREWALL_FORCE_MOCK"] = "1"
        os.environ["TOKENFIREWALL_CHEAP_MODEL"] = "gpt-4o-mini"
        os.environ["TOKENFIREWALL_DEFAULT_MODEL"] = "gpt-4o"
        os.environ["TOKENFIREWALL_STRONG_MODEL"] = "gpt-4o"
        os.environ.pop("TOKENFIREWALL_DISABLE", None)

        client = BenchmarkLLMClient(answers)
        set_llm_client(client)
        router_config = router_config_from_env()

        baseline_total = 0
        baseline_cost = 0.0
        previous_total = 0
        previous_cost = 0.0
        optimized_total = 0
        optimized_cost = 0.0
        cache_hits = 0
        tool_bypasses = 0
        budget_blocks = 0
        quality_passes = 0
        optimized_latencies: list[float] = []
        overheads: list[float] = []
        selected_model_tiers: list[int] = []
        selected_model_counts: dict[str, int] = {}
        per_case: list[dict[str, Any]] = []

        for case in cases:
            query = str(case["query"])
            max_turns = int(case.get("max_turns", 3))
            max_output_tokens = int(case.get("max_output_tokens", 400))
            chat_history = list(case.get("chat_history", []))
            notes = case.get("notes")
            notes_hash = case.get("notes_hash")

            baseline_messages = _build_messages(case)
            baseline_response = client.complete(
                baseline_messages,
                router_config.default_model,
                max_output_tokens=max_output_tokens,
            )
            baseline_tokens = baseline_response.input_tokens + baseline_response.output_tokens
            baseline_total += baseline_tokens
            baseline_cost += estimate_cost_usd(
                router_config.default_model,
                baseline_response.input_tokens,
                baseline_response.output_tokens,
            )

            os.environ["TOKENFIREWALL_CACHE_PATH"] = str(temp_dir / "previous-cache.sqlite3")
            os.environ["TOKENFIREWALL_USAGE_PATH"] = str(temp_dir / "previous-usage.sqlite3")
            previous = ask(
                query,
                chat_history=chat_history,
                notes=notes,
                model=router_config.default_model,
                notes_hash=notes_hash,
                max_turns=max_turns,
                max_output_tokens=max_output_tokens,
                force=True,
                long_context_threshold=1_000_000,
            )
            previous_tokens = int(previous["optimized_tokens"])
            previous_total += previous_tokens
            previous_cost += float(previous["estimated_cost_usd"])

            os.environ["TOKENFIREWALL_CACHE_PATH"] = str(cache_path)
            os.environ["TOKENFIREWALL_USAGE_PATH"] = str(temp_dir / "usage.sqlite3")
            optimized = ask(
                query,
                chat_history=chat_history,
                notes=notes,
                notes_hash=notes_hash,
                max_turns=max_turns,
                max_output_tokens=max_output_tokens,
                force=True,
            )

            optimized_tokens = int(optimized["optimized_tokens"])
            optimized_total += optimized_tokens
            optimized_cost += float(optimized["estimated_cost_usd"])
            cache_hits += int(bool(optimized["cache_hit"]))
            tool_bypasses += int(optimized["tool_used"] is not None)
            budget_blocks += int(bool(optimized["budget_blocked"]))
            quality_pass = _quality_passed(case, str(optimized["answer"]))
            quality_passes += int(quality_pass)
            optimized_latency = float(optimized["latency_ms"])
            baseline_latency = float(baseline_response.latency_ms)
            optimized_latencies.append(optimized_latency)
            overheads.append(max(0.0, optimized_latency - baseline_latency))
            selected_model = str(optimized["selected_model"])
            selected_model_counts[selected_model] = selected_model_counts.get(selected_model, 0) + 1
            if selected_model == "tool":
                selected_model_tiers.append(0)
            elif selected_model == router_config.cheap_model:
                selected_model_tiers.append(1)
            elif selected_model == router_config.strong_model:
                selected_model_tiers.append(3)
            else:
                selected_model_tiers.append(2)
            per_case.append(
                {
                    "id": case.get("id"),
                    "category": case.get("category"),
                    "baseline_tokens": baseline_tokens,
                    "previous_tokens": previous_tokens,
                    "optimized_tokens": optimized_tokens,
                    "saved_tokens": baseline_tokens - optimized_tokens,
                    "cache_hit": optimized["cache_hit"],
                    "tool_used": optimized["tool_used"],
                    "selected_model": optimized["selected_model"],
                    "latency_ms": optimized["latency_ms"],
                    "quality_passed": quality_pass,
                }
            )

        saved_tokens = baseline_total - optimized_total
        previous_saved_tokens = baseline_total - previous_total
        cost_saved = baseline_cost - optimized_cost
        previous_cost_saved = baseline_cost - previous_cost
        saved_percent = _percent(saved_tokens, baseline_total)
        previous_saved_percent = _percent(previous_saved_tokens, baseline_total)
        summary = {
            "cases": len(cases),
            "total_baseline_tokens": baseline_total,
            "total_previous_tokens": previous_total,
            "total_optimized_tokens": optimized_total,
            "saved_tokens": saved_tokens,
            "saved_percent": saved_percent,
            "previous_saved_percent": previous_saved_percent,
            "baseline_cost_usd": baseline_cost,
            "previous_cost_usd": previous_cost,
            "optimized_cost_usd": optimized_cost,
            "cost_saved_usd": cost_saved,
            "cost_saved_percent": (cost_saved / baseline_cost * 100.0) if baseline_cost else 0.0,
            "additional_cost_saved_usd": max(0.0, previous_cost - optimized_cost),
            "additional_cost_saved_percent": (
                max(0.0, previous_cost - optimized_cost) / previous_cost * 100.0
                if previous_cost
                else 0.0
            ),
            "cache_hit_rate": _rate(cache_hits, len(cases)),
            "tool_bypass_rate": _rate(tool_bypasses, len(cases)),
            "budget_blocks": budget_blocks,
            "selected_model_counts": selected_model_counts,
            "avg_selected_model_tier": (
                statistics.fmean(selected_model_tiers) if selected_model_tiers else 0.0
            ),
            "avg_latency_ms": statistics.fmean(optimized_latencies) if optimized_latencies else 0.0,
            "p50_latency_ms": _p50(optimized_latencies),
            "p90_latency_ms": _p90(optimized_latencies),
            "p90_latency_overhead_ms": _p90(overheads),
            "quality_check_pass_rate": _rate(quality_passes, len(cases)),
            "thresholds": {
                "min_saved_percent": MIN_SAVED_PERCENT,
                "max_p90_latency_overhead_ms": MAX_P90_OVERHEAD_MS,
                "min_quality_check_pass_rate": MIN_QUALITY_PASS_RATE,
            },
            "llm_calls": client.calls,
            "per_case": per_case,
        }
        (Path(__file__).with_name("benchmark_results.json")).write_text(
            json.dumps(summary, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return summary


def main() -> int:
    summary = run_benchmark()
    _print_table(summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "per_case"}, indent=2))

    failures: list[str] = []
    if summary["saved_percent"] < MIN_SAVED_PERCENT:
        failures.append(f"saved_percent {summary['saved_percent']:.2f}% < {MIN_SAVED_PERCENT:.2f}%")
    if summary["p90_latency_overhead_ms"] > MAX_P90_OVERHEAD_MS:
        failures.append(
            f"p90 latency overhead {summary['p90_latency_overhead_ms']:.2f}ms > "
            f"{MAX_P90_OVERHEAD_MS:.2f}ms"
        )
    if summary["quality_check_pass_rate"] < MIN_QUALITY_PASS_RATE:
        failures.append(
            f"quality_check_pass_rate {summary['quality_check_pass_rate']:.4f} < "
            f"{MIN_QUALITY_PASS_RATE:.4f}"
        )
    if failures:
        print("FAIL: " + "; ".join(failures), file=sys.stderr)
        return 1
    print("PASS: benchmark thresholds met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
