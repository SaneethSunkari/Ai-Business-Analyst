from tokenfirewall.metrics import calculate_savings


def test_saved_tokens_and_percent_are_correct() -> None:
    result = calculate_savings(
        baseline_input_tokens=80,
        baseline_output_tokens=20,
        optimized_input_tokens=30,
        optimized_output_tokens=20,
    )

    assert result["baseline_total"] == 100
    assert result["optimized_total"] == 50
    assert result["saved_tokens"] == 50
    assert result["saved_percent"] == 50.0


def test_no_divide_by_zero() -> None:
    result = calculate_savings(0, 0, 0, 0)

    assert result["baseline_total"] == 0
    assert result["saved_tokens"] == 0
    assert result["saved_percent"] == 0.0
