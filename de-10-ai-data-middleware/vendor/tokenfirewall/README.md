# TokenFirewall

TokenFirewall is a small Python decision layer that runs before an LLM call. It helps reduce unnecessary paid usage by counting tokens, checking a persistent SQLite cache, safely answering simple local tool requests, pruning older context, choosing a cheaper model when deterministic rules allow it, bounding output tokens, applying budget guardrails, and retrying once with protected context if a pruned call fails.

It is intentionally conservative. It does not add semantic embeddings, vector cache, prompt compression, complex summarization, or real web search.

## Installation

```bash
python -m pip install -e .
```

For development:

```bash
python -m pip install -e ".[dev]"
pytest
```

## Library Usage

```python
from tokenfirewall import ask

result = ask(
    query="What is 234 * 98?",
    chat_history=[],
    mode="short",
    max_turns=5,
    debug=True,
)

print(result["answer"])
print(result["baseline_tokens"], result["optimized_tokens"])
print(result["selected_model"], result["estimated_cost_usd"])
print(result["saved_tokens"])
```

`chat_history` should contain previous structured messages. TokenFirewall appends the current `query` as the newest user message.

## CLI Usage

```bash
python -m tokenfirewall ask "What is 234 * 98"
python -m tokenfirewall ask "What is the capital of France?" --chat chat.json --debug
python -m tokenfirewall ask "Explain these notes" --notes notes.txt --mode short --max-turns 5
python -m tokenfirewall ask "Deeply review this design" --mode deep --max-cost 0.02
python -m tokenfirewall ask --budget-status
python -m tokenfirewall usage
python -m tokenfirewall server --port 8787
```

If no real LLM client is configured, TokenFirewall uses a deterministic mock client so local examples and tests run without credentials. The math example is answered by the safe tool bypass and does not call an LLM.

## Debug Mode

Pass `debug=True` in Python or `--debug` in the CLI to include the full decision payload:

- strategy steps
- cache key metadata
- full and pruned message counts
- baseline and optimized token counts
- cache or tool details when relevant
- fallback errors if a retry was needed

Every `ask()` response includes `baseline_tokens`, `optimized_tokens`, `saved_tokens`, `saved_percent`, `cache_hit`, `tool_used`, `latency_ms`, `strategy`, `fallback_used`, `selected_model`, `estimated_cost_usd`, `daily_tokens_used`, `monthly_tokens_used`, and `budget_blocked`. CLI `--debug` prints the full JSON payload.

## Personal Cost Workflow

Set model and budget defaults for heavy personal usage:

```bash
export TOKENFIREWALL_CHEAP_MODEL=gpt-4o-mini
export TOKENFIREWALL_DEFAULT_MODEL=gpt-4o
export TOKENFIREWALL_STRONG_MODEL=gpt-4o
export TOKENFIREWALL_DAILY_TOKEN_BUDGET=50000
export TOKENFIREWALL_MONTHLY_TOKEN_BUDGET=1000000
```

Use output modes to cap response length:

- `--mode short` uses `150` output tokens
- `--mode normal` uses `400` output tokens
- `--mode deep` uses `1200` output tokens

Use `--max-cost` to block a request whose estimated preflight cost is too high. Use `--force` only when you intentionally want to override a budget or cost block.

The `usage` command reports total requests, cache hits, tool bypasses, LLM calls, estimated tokens saved, estimated cost saved, and top strategies.

## Local Gateway

Run TokenFirewall as a localhost gateway:

```bash
cd /Users/saneethsunkari/Desktop/plan/Token
python -m tokenfirewall server --host 127.0.0.1 --port 8787
```

OpenAI-compatible clients can point at:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8787/v1
export OPENAI_API_KEY=your_real_openai_key
```

Anthropic-compatible clients can point at:

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8787
export ANTHROPIC_API_KEY=your_real_anthropic_key
```

For Claude Code, start the gateway in one terminal, then start Claude Code in another terminal with `ANTHROPIC_BASE_URL` set to the local gateway. Plain text message requests go through TokenFirewall cache, tool bypass, pruning, budget, and usage analytics.

Important: Claude Code often uses Anthropic tool-use blocks. By default, TokenFirewall passes those tool-use requests through to Anthropic to preserve correctness, because collapsing tool calls into plain text would break the agent loop. Tool-use passthrough is safer but saves fewer tokens for those specific calls. Set `TOKENFIREWALL_GATEWAY_PASSTHROUGH_TOOLS=0` only if you understand the compatibility tradeoff.

Useful gateway environment variables:

```bash
export TOKENFIREWALL_GATEWAY_MODE=short
export TOKENFIREWALL_FORCE_MOCK=1
export TOKENFIREWALL_UPSTREAM_ANTHROPIC_BASE_URL=https://api.anthropic.com
export TOKENFIREWALL_UPSTREAM_OPENAI_BASE_URL=https://api.openai.com/v1
```

## Benchmarks

Run the benchmark suite:

```bash
python benchmarks/benchmark_token_savings.py
```

The benchmark compares a direct full-context baseline, a previous-style TokenFirewall path, and the new optimized path with routing/cost metrics on at least 100 realistic cases. It prints a console table and writes JSON results to `benchmarks/benchmark_results.json`.

Go/no-go thresholds:

- `saved_percent >= 60`
- `p90_latency_overhead_ms <= 150`
- `quality_check_pass_rate >= 0.98`

Interpretation:

- `total_baseline_tokens` is the direct full-context mock LLM path.
- `total_previous_tokens` approximates the prior cache/tool/prune behavior with a fixed default model.
- `total_optimized_tokens` is the cache/tool/pruned TokenFirewall path.
- `cost_saved_percent` includes both token savings and deterministic model routing.
- `additional_cost_saved_percent` compares the new optimized path against the previous-style TokenFirewall path.
- `cache_hit_rate` shows repeated exact and safe factual-paraphrase reuse.
- `tool_bypass_rate` shows deterministic math and allowed file reads that skipped the LLM.
- `quality_check_pass_rate` is a deterministic expected-output check from the workload. JSON reports rates as fractions; the console table renders them as percentages.

The included workload currently clears the target because it contains repeated support/factual/document requests, deterministic arithmetic, allowed file reads, and long histories where pruning is safe. Achieving `>=60%` is workload-dependent: generic creative chats with little repetition and few deterministic-tool opportunities may not reach that savings level.

## Safety Notes

- Cache keys are based on canonical structured messages, model, system content, notes hash, firewall version, and tool registry version.
- Cache keys also include response `mode` and a content hash, so repeated personal prompts can reuse safely without collapsing unrelated content.
- Cache normalization trims and collapses whitespace. It does not lowercase or remove punctuation globally.
- Factual paraphrase cache canonicalization is intentionally narrow and only covers high-confidence patterns such as `capital of France` and `Tell me the capital of France`.
- Personal prompt canonicalization is intentionally narrow and only covers patterns such as `summarize this: <same content>` and `rewrite this: <same content>`.
- Long-context protection avoids sending full context when input exceeds the configured threshold; notes are retained only when simple keyword overlap suggests relevance.
- The math tool uses an AST whitelist and never evaluates raw user text.
- The file-read tool is disabled unless `TOKENFIREWALL_ALLOWED_DIR` is set or a `ToolRegistry` is created with `allowed_dir`.
- File reads are constrained with resolved paths and cannot escape the configured allowed directory.
- Built-in tools do not make network calls.
- The gateway can make upstream LLM API calls when you provide provider API keys. Built-in deterministic tools still do not make network calls.

## What It Does Not Do

TokenFirewall does not perform semantic retrieval, embeddings, vector cache, prompt compression, complex summarization, or external search. Its model routing is deterministic and rule-based only; it does not call an LLM to route.

## Why Search Is Mocked In MVP

Search can silently introduce network access, freshness assumptions, data leakage, vendor dependencies, and failure modes that are outside this MVP's safety goals. TokenFirewall therefore does not ship a real search tool. If you need search-like behavior, explicitly register your own tool:

```python
from tokenfirewall.tools import register_tool

def detect_search(query: str) -> bool:
    return query.startswith("search:")

def execute_search(query: str) -> dict:
    return {"answer": "mock search result"}

register_tool("search", detect_search, execute_search)
```

## Plug In A Real LLM Client

Tests and local use do not require an API key. To use your own client, implement `complete()` and register it:

```python
from tokenfirewall.llm import LLMResponse, set_llm_client

class MyClient:
    def complete(self, messages, model, max_output_tokens=300):
        # Call your provider here.
        answer = "provider response"
        return LLMResponse(
            answer=answer,
            input_tokens=123,
            output_tokens=20,
            latency_ms=250,
        )

set_llm_client(MyClient())
```

An optional `OpenAIChatClient` adapter is included. If the `openai` package is installed and `OPENAI_API_KEY` is present, the default wrapper can use it. Set `TOKENFIREWALL_FORCE_MOCK=1` to force the local mock path.

## Cache Persistence

By default, cache data is stored at:

```text
~/.cache/tokenfirewall/cache.sqlite3
```

Override it with:

```bash
export TOKENFIREWALL_CACHE_PATH=/path/to/cache.sqlite3
```

## Known Limitations

- Token counts are approximate unless a real provider returns usage numbers.
- Context pruning keeps important/system/tagged messages and recent exchanges, but it does not summarize removed context.
- Tool bypass is intentionally narrow. Unsupported requests fall through to the LLM path.
- The bundled mock LLM is only for tests and local wiring checks.
