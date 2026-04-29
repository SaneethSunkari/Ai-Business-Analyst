from tokenfirewall.pruner import prune_context


def test_system_message_retained() -> None:
    messages = [
        {"role": "system", "content": "Rules"},
        {"role": "user", "content": "Old"},
        {"role": "assistant", "content": "Old answer"},
        {"role": "user", "content": "New"},
    ]

    pruned = prune_context(messages, max_turns=1)

    assert {"role": "system", "content": "Rules"} in pruned


def test_important_messages_retained() -> None:
    messages = [
        {"role": "user", "content": "Important", "important": True},
        {"role": "assistant", "content": "Old"},
        {"role": "user", "content": "New"},
    ]

    pruned = prune_context(messages, max_turns=1)

    assert any(message["content"] == "Important" for message in pruned)


def test_tags_retained() -> None:
    messages = [
        {"role": "user", "content": "Keep tag", "tags": ["billing"]},
        {"role": "assistant", "content": "Old"},
        {"role": "user", "content": "New"},
    ]

    pruned = prune_context(messages, max_turns=1, important_tags={"billing"})

    assert any(message["content"] == "Keep tag" for message in pruned)


def test_last_n_exchanges_retained() -> None:
    messages = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]

    pruned = prune_context(messages, max_turns=2)
    contents = [message["content"] for message in pruned]

    assert contents == ["u2", "a2", "u3", "a3"]


def test_original_order_preserved() -> None:
    messages = [
        {"role": "user", "content": "important", "important": True},
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "older answer"},
        {"role": "user", "content": "newer"},
        {"role": "assistant", "content": "newer answer"},
    ]

    pruned = prune_context(messages, max_turns=1)
    contents = [message["content"] for message in pruned]

    assert contents == ["important", "newer", "newer answer"]


def test_duplicates_removed() -> None:
    duplicate = {"role": "system", "content": "Rules"}
    messages = [
        duplicate,
        dict(duplicate),
        {"role": "user", "content": "New"},
    ]

    pruned = prune_context(messages, max_turns=1)

    assert pruned.count(duplicate) == 1


def test_empty_history_safe() -> None:
    assert prune_context([], max_turns=5) == []


def test_auto_important_durable_facts_retained() -> None:
    messages = [
        {"role": "user", "content": "my name is Saneeth"},
        {"role": "assistant", "content": "Nice to meet you."},
        {"role": "user", "content": "older"},
        {"role": "assistant", "content": "older answer"},
        {"role": "user", "content": "new"},
    ]

    pruned = prune_context(messages, max_turns=1)

    assert any(message["content"] == "my name is Saneeth" for message in pruned)
    durable = next(message for message in pruned if message["content"] == "my name is Saneeth")
    assert durable["important"] is True
    assert "durable_fact" in durable["tags"]


def test_auto_important_supported_fact_patterns_retained() -> None:
    messages = [
        {"role": "user", "content": "I work at Northstar Analytics"},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "project name is Apollo Ledger"},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "remember replies should be concise"},
        {"role": "assistant", "content": "Noted."},
        {"role": "user", "content": "new"},
    ]

    pruned = prune_context(messages, max_turns=1)
    contents = {message["content"] for message in pruned}

    assert "I work at Northstar Analytics" in contents
    assert "project name is Apollo Ledger" in contents
    assert "remember replies should be concise" in contents


def test_auto_important_does_not_overtag_my_friend() -> None:
    messages = [
        {"role": "user", "content": "my friend is named Saneeth"},
        {"role": "assistant", "content": "Got it."},
        {"role": "user", "content": "new"},
    ]

    pruned = prune_context(messages, max_turns=1)

    assert all(message["content"] != "my friend is named Saneeth" for message in pruned)
