"""Task script loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TaskValidationError(ValueError):
    """Raised when a task script does not match the supported format."""


_ACTIONS = {"goto", "click", "fill", "wait", "screenshot", "extract", "pause"}
_SELECTOR_ACTIONS = {"click", "fill", "extract"}
_TOP_LEVEL_KEYS = {"name", "base_url", "retry_policy", "steps", "inputs"}


def load_task(path: str | Path) -> dict[str, Any]:
    """Read JSON and validate it without starting a browser."""
    task_path = Path(path)
    try:
        data = json.loads(task_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TaskValidationError(f"Task file not found: {task_path}") from exc
    except json.JSONDecodeError as exc:
        raise TaskValidationError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    validate_task(data)
    data["_source_path"] = str(task_path.resolve())
    return data


def validate_task(task: Any) -> None:
    """Validate a task object. Errors identify the offending step when possible."""
    if not isinstance(task, dict):
        raise TaskValidationError("Task must be a JSON object")
    unknown = sorted(set(task) - _TOP_LEVEL_KEYS)
    if unknown:
        raise TaskValidationError(f"Unknown task field: {unknown[0]}")
    if not isinstance(task.get("name"), str) or not task["name"].strip():
        raise TaskValidationError("Task field 'name' must be a non-empty string")
    if "base_url" in task and (not isinstance(task["base_url"], str) or not task["base_url"].strip()):
        raise TaskValidationError("Task field 'base_url' must be a non-empty string")
    if "inputs" in task and not isinstance(task["inputs"], dict):
        raise TaskValidationError("Task field 'inputs' must be an object")

    if "retry_policy" not in task:
        raise TaskValidationError("Task field 'retry_policy' is required to make retries explicit")
    policy = task["retry_policy"]
    if not isinstance(policy, dict):
        raise TaskValidationError("Task field 'retry_policy' must be an object")
    if set(policy) - {"max_attempts", "backoff_ms"}:
        raise TaskValidationError("Retry policy supports only max_attempts and backoff_ms")
    for field in ("max_attempts", "backoff_ms"):
        if field not in policy:
            raise TaskValidationError(f"retry_policy.{field} is required")
    _positive_int(policy["max_attempts"], "retry_policy.max_attempts", allow_zero=False)
    _positive_int(policy["backoff_ms"], "retry_policy.backoff_ms", allow_zero=True)

    steps = task.get("steps")
    if not isinstance(steps, list) or not steps:
        raise TaskValidationError("Task field 'steps' must be a non-empty array")
    seen_ids: set[str] = set()
    for index, step in enumerate(steps, start=1):
        label = f"step {index}"
        if not isinstance(step, dict):
            raise TaskValidationError(f"{label} must be an object")
        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id.strip():
            raise TaskValidationError(f"{label} has an invalid or missing id")
        label = f"step '{step_id}'"
        if step_id in seen_ids:
            raise TaskValidationError(f"{label} duplicates an earlier step id")
        seen_ids.add(step_id)
        action = step.get("action")
        if not isinstance(action, str) or action not in _ACTIONS:
            raise TaskValidationError(f"{label} has unsupported action {action!r}")
        allowed = {"id", "action", "timeout_ms", "expected_text"}
        if action == "goto":
            allowed.add("url")
        if action in _SELECTOR_ACTIONS | {"wait"}:
            allowed.add("selector")
        if action == "fill":
            allowed.add("value")
        if action == "wait":
            allowed.update({"duration_ms", "state"})
        if action == "extract":
            allowed.update({"mode", "attribute"})
        if action == "pause":
            allowed.add("message")
        unexpected = sorted(set(step) - allowed)
        if unexpected:
            raise TaskValidationError(f"{label} has unknown field '{unexpected[0]}'")
        timeout = step.get("timeout_ms", 5000)
        _positive_int(timeout, f"{label}.timeout_ms", allow_zero=False)
        expected = step.get("expected_text")
        if expected is not None and not isinstance(expected, str):
            raise TaskValidationError(f"{label}.expected_text must be a string")
        if action == "goto":
            if not isinstance(step.get("url"), str) or not step["url"].strip():
                raise TaskValidationError(f"{label}.url must be a non-empty string")
        if action in _SELECTOR_ACTIONS:
            _require_string(step, "selector", label)
        if action == "fill" and not isinstance(step.get("value"), str):
            raise TaskValidationError(f"{label}.value must be a string")
        if action == "wait":
            has_duration = "duration_ms" in step
            has_selector = "selector" in step
            if has_duration == has_selector:
                raise TaskValidationError(f"{label} must set exactly one of duration_ms or selector")
            if has_duration:
                _positive_int(step["duration_ms"], f"{label}.duration_ms", allow_zero=True)
            else:
                _require_string(step, "selector", label)
                state = step.get("state", "visible")
                if not isinstance(state, str) or state not in {"visible", "hidden"}:
                    raise TaskValidationError(f"{label}.state must be 'visible' or 'hidden'")
        if action == "extract":
            mode = step.get("mode", "text")
            if not isinstance(mode, str) or mode not in {"text", "attribute"}:
                raise TaskValidationError(f"{label}.mode must be 'text' or 'attribute'")
            if mode == "attribute":
                _require_string(step, "attribute", label)
            elif "attribute" in step:
                raise TaskValidationError(f"{label}.attribute requires mode 'attribute'")
        if action == "pause" and "message" in step and not isinstance(step["message"], str):
            raise TaskValidationError(f"{label}.message must be a string")


def _positive_int(value: Any, field: str, *, allow_zero: bool) -> None:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        relation = "non-negative" if allow_zero else "positive"
        raise TaskValidationError(f"{field} must be a {relation} integer")


def _require_string(value: dict[str, Any], key: str, label: str) -> None:
    if not isinstance(value.get(key), str) or not value[key].strip():
        raise TaskValidationError(f"{label}.{key} must be a non-empty string")
