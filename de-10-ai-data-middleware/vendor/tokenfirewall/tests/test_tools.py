from tokenfirewall.tools import ToolRegistry, run_tools


def test_safe_math_works() -> None:
    result = run_tools("2 + 3 * 4")

    assert result is not None
    assert result["name"] == "math"
    assert result["answer"] == "14"


def test_unsafe_math_is_rejected() -> None:
    assert run_tools("__import__('os').system('echo unsafe')") is None
    assert run_tools("2 + secret") is None


def test_natural_language_math_extraction_works() -> None:
    result = run_tools("what is 234 * 98?")

    assert result is not None
    assert result["name"] == "math"
    assert result["answer"] == "22932"


def test_word_math_and_percent_of_work() -> None:
    word_result = run_tools("compute 234 times 98")
    percent_result = run_tools("what is 12.5% of 800")

    assert word_result is not None
    assert word_result["answer"] == "22932"
    assert percent_result is not None
    assert percent_result["answer"] == "100"


def test_math_rejects_calls_attributes_names_and_paths() -> None:
    unsafe_queries = [
        "calculate open('/tmp/x')",
        "what is (1).__class__",
        "compute amount * 4",
        "calculate /etc/passwd",
    ]

    for query in unsafe_queries:
        assert run_tools(query) is None


def test_file_read_rejects_path_traversal(tmp_path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    registry = ToolRegistry(allowed_dir=str(allowed))

    result = registry.run_tools("read file ../secret.txt")

    assert result is not None
    assert result["name"] == "file-read"
    assert "outside allowed_dir" in result["error"]
