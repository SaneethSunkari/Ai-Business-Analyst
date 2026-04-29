from tokenfirewall.router import RouterConfig, route_query


def test_router_sends_tool_candidates_to_tool_route() -> None:
    decision = route_query("compute 234 times 98")

    assert decision.selected_model == "tool"
    assert decision.reason == "tool_candidate"


def test_router_sends_factual_to_cheap_model() -> None:
    config = RouterConfig(cheap_model="cheap", default_model="default", strong_model="strong")

    decision = route_query("What is the capital of France?", config=config)

    assert decision.selected_model == "cheap"
    assert decision.reason == "cheap_rule"


def test_router_sends_debugging_to_strong_model() -> None:
    config = RouterConfig(cheap_model="cheap", default_model="default", strong_model="strong")

    decision = route_query("Debug this Python traceback", config=config)

    assert decision.selected_model == "strong"
    assert decision.reason == "strong_rule"


def test_router_honors_user_override() -> None:
    decision = route_query("What is the capital of France?", model_override="manual-model")

    assert decision.selected_model == "manual-model"
    assert decision.reason == "user_override"
