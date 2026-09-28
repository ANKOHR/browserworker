from __future__ import annotations


def sample_task(base_url: str) -> dict:
    return {
        "name": "fixture-login-search-and-retry",
        "base_url": base_url,
        "inputs": {"username": "Henry", "query": "browser"},
        "retry_policy": {"max_attempts": 2, "backoff_ms": 10},
        "steps": [
            {"id": "open-login", "action": "goto", "url": "/login"},
            {"id": "fill-username", "action": "fill", "selector": "#username", "value": "Henry"},
            {"id": "fill-password", "action": "fill", "selector": "#password", "value": "fixture-password"},
            {
                "id": "submit-login",
                "action": "click",
                "selector": "#login-submit",
                "expected_text": "Welcome, Henry",
            },
            {
                "id": "extract-welcome",
                "action": "extract",
                "selector": "#welcome",
                "expected_text": "Welcome, Henry",
            },
            {"id": "open-search", "action": "goto", "url": "/search"},
            {"id": "fill-query", "action": "fill", "selector": "#query", "value": "browser"},
            {
                "id": "submit-search",
                "action": "click",
                "selector": "#search-submit",
                "expected_text": "Results for",
            },
            {
                "id": "extract-result",
                "action": "extract",
                "selector": ".result-title",
                "expected_text": "Deterministic Browser Tasks",
            },
            {"id": "open-flaky", "action": "goto", "url": "/flaky"},
            {
                "id": "retry-flaky-button",
                "action": "click",
                "selector": "#flaky-button",
                "timeout_ms": 350,
                "expected_text": "Flaky action complete",
            },
            {
                "id": "extract-flaky-status",
                "action": "extract",
                "selector": "#flaky-status",
                "expected_text": "Flaky action complete",
            },
        ],
    }
