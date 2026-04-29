import os
import subprocess
import sys

from tokenfirewall.cache import (
    cache_get,
    cache_set,
    canonicalize_prompt_for_cache,
    make_key,
)


def test_same_structured_messages_produce_same_key() -> None:
    messages_a = [{"role": "user", "content": "  Hello   world?  "}]
    messages_b = [{"role": "user", "content": "Hello world?"}]

    assert make_key(messages_a, "gpt-4o-mini", None, "0.1.0", "tools") == make_key(
        messages_b,
        "gpt-4o-mini",
        None,
        "0.1.0",
        "tools",
    )


def test_different_model_produces_different_key() -> None:
    messages = [{"role": "user", "content": "Hello"}]

    assert make_key(messages, "model-a", None, "0.1.0", "tools") != make_key(
        messages,
        "model-b",
        None,
        "0.1.0",
        "tools",
    )


def test_different_system_message_produces_different_key() -> None:
    messages_a = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Hello"},
    ]
    messages_b = [
        {"role": "system", "content": "Be detailed."},
        {"role": "user", "content": "Hello"},
    ]

    assert make_key(messages_a, "gpt-4o-mini", None, "0.1.0", "tools") != make_key(
        messages_b,
        "gpt-4o-mini",
        None,
        "0.1.0",
        "tools",
    )


def test_different_notes_hash_produces_different_key() -> None:
    messages = [{"role": "user", "content": "Hello"}]

    assert make_key(messages, "gpt-4o-mini", "notes-a", "0.1.0", "tools") != make_key(
        messages,
        "gpt-4o-mini",
        "notes-b",
        "0.1.0",
        "tools",
    )


def test_cache_key_includes_mode() -> None:
    messages = [{"role": "user", "content": "Explain caching."}]

    assert make_key(messages, "gpt-4o-mini", None, "0.1.0", "tools", mode="short") != make_key(
        messages,
        "gpt-4o-mini",
        None,
        "0.1.0",
        "tools",
        mode="deep",
    )


def test_allowed_factual_paraphrases_share_key() -> None:
    messages_a = [{"role": "user", "content": "What is the capital of France?"}]
    messages_b = [{"role": "user", "content": "Tell me the capital of France"}]
    messages_c = [{"role": "user", "content": "capital of France"}]

    key_a = make_key(messages_a, "gpt-4o-mini", None, "0.1.0", "tools")
    assert key_a == make_key(messages_b, "gpt-4o-mini", None, "0.1.0", "tools")
    assert key_a == make_key(messages_c, "gpt-4o-mini", None, "0.1.0", "tools")


def test_disallowed_prompts_do_not_share_canonical_form() -> None:
    assert (
        canonicalize_prompt_for_cache("Tell me about the capital of France")
        != canonicalize_prompt_for_cache("capital of France")
    )
    assert (
        canonicalize_prompt_for_cache("What is the weather in France?")
        != canonicalize_prompt_for_cache("capital of France")
    )


def test_personal_prompt_canonicalization_uses_content_hash() -> None:
    first = canonicalize_prompt_for_cache("Please summarize this: Alpha beta gamma delta.")
    second = canonicalize_prompt_for_cache("summarize this Alpha beta gamma delta.")
    different = canonicalize_prompt_for_cache("summarize this Different content appears here.")

    assert first == second
    assert first != different
    assert "Alpha beta" not in first


def test_cache_hit_and_miss_behavior(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    key = "abc123"

    assert cache_get(key, path=str(db_path)) is None

    cache_set(
        key,
        response="hello",
        input_tokens=10,
        output_tokens=2,
        strategy=["llm"],
        model="gpt-4o-mini",
        path=str(db_path),
    )

    cached = cache_get(key, path=str(db_path))
    assert cached is not None
    assert cached["response"] == "hello"
    assert cached["input_tokens"] == 10
    assert cached["output_tokens"] == 2
    assert cached["strategy"] == ["llm"]
    assert cached["model"] == "gpt-4o-mini"


def test_cache_persists_across_process_runs(tmp_path) -> None:
    db_path = tmp_path / "cache.sqlite3"
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    writer = """
from tokenfirewall.cache import cache_set
cache_set('persist-key', 'persisted', 1, 2, ['llm'], 'gpt-4o-mini', path=r'{db}')
""".format(db=db_path)
    reader = """
from tokenfirewall.cache import cache_get
cached = cache_get('persist-key', path=r'{db}')
print(cached['response'] if cached else 'MISS')
""".format(db=db_path)

    subprocess.run([sys.executable, "-c", writer], check=True, env=env)
    completed = subprocess.run(
        [sys.executable, "-c", reader],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )

    assert completed.stdout.strip() == "persisted"
