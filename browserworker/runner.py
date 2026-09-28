"""Real Chromium execution with deterministic retries and an auditable history."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .task import validate_task


HONESTY_STATEMENT = (
    "BrowserWorker runs explicit, human-written scripts with deterministic retries and human takeover. "
    "It does not decide what to do or understand pages."
)


class StepExecutionError(RuntimeError):
    """A stable, user-readable step failure."""


class RunControl:
    """Thread-safe pause and resume control for a live run.

    A caller can wait for a handoff with ``wait_until_paused`` and then call
    ``resume`` after the human has finished working in the visible browser.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._paused = False
        self._resume_requested = False

    @property
    def paused(self) -> bool:
        with self._condition:
            return self._paused

    def wait_until_paused(self, timeout: float | None = None) -> bool:
        """Wait for a live handoff. Return false if the timeout expires."""
        with self._condition:
            return self._condition.wait_for(lambda: self._paused, timeout=timeout)

    def resume(self) -> None:
        """Signal that the human handoff is complete."""
        with self._condition:
            if self._paused:
                self._resume_requested = True
                self._condition.notify_all()

    def _handoff(
        self,
        reason: str,
        step: dict[str, Any],
        page,
        callback: Callable[["RunControl", Any, dict[str, Any], str], None] | None,
    ) -> list[dict[str, str]]:
        events = [{"event": "takeover_paused", "timestamp": _timestamp(), "reason": reason}]
        with self._condition:
            self._paused = True
            self._resume_requested = False
            self._condition.notify_all()
        if callback is not None:
            callback(self, page, step, reason)
        with self._condition:
            self._condition.wait_for(lambda: self._resume_requested)
            self._paused = False
            self._resume_requested = False
        events.append({"event": "takeover_resumed", "timestamp": _timestamp(), "reason": reason})
        return events


@dataclass
class RunResult:
    """Files and in-memory records produced by one browser run."""

    output_dir: Path
    status: str
    action_history: list[dict[str, Any]] = field(default_factory=list)
    step_metrics: list[dict[str, Any]] = field(default_factory=list)
    extracted_values: dict[str, Any] = field(default_factory=dict)
    run_data: dict[str, Any] = field(default_factory=dict)


def run_task(
    task: dict[str, Any],
    output_dir: str | Path,
    *,
    headed: bool = False,
    takeover_on_failure: bool = False,
    control: RunControl | None = None,
    takeover_callback: Callable[[RunControl, Any, dict[str, Any], str], None] | None = None,
    audit_path: str | Path | None = None,
) -> RunResult:
    """Execute a validated task using real Playwright Chromium.

    Each step gets one screenshot, including a failed step. Retry count and
    backoff come from the task's ``retry_policy`` object and contain no jitter.
    Elapsed timings are stored as run metrics, separate from deterministic
    action outcomes. The audit file is append-only JSON Lines.
    """
    clean_task = {key: value for key, value in task.items() if not key.startswith("_")}
    validate_task(clean_task)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    screenshots_dir = out / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)
    run_control = control or RunControl()
    retry_policy = task["retry_policy"]
    max_attempts = retry_policy["max_attempts"]
    backoff_ms = retry_policy["backoff_ms"]
    history: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    extracted: dict[str, Any] = {}
    run_status = "completed"
    task_source = task.get("_source_path")
    started_at = _timestamp()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not headed,
            args=["--disable-gpu", "--disable-dev-shm-usage", "--use-gl=swiftshader-webgl"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            device_scale_factor=1,
            locale="en-US",
            timezone_id="UTC",
            color_scheme="light",
            reduced_motion="reduce",
        )
        page = context.new_page()
        base_url = task.get("base_url", "")

        try:
            for index, step in enumerate(task["steps"], start=1):
                step_id = step["id"]
                action = step["action"]
                selector = step.get("selector")
                slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", step_id).strip("-").lower() or f"step-{index}"
                screenshot_rel = f"screenshots/{index:03d}-{slug}.png"
                screenshot_abs = out / screenshot_rel
                timeout_ms = step.get("timeout_ms", 5000)
                step_start = time.perf_counter()
                attempt_log: list[dict[str, Any]] = []
                takeover_events: list[dict[str, str]] = []
                step_value: Any = None
                step_success = False
                attempt_limit = max_attempts
                taken_over = False

                while len(attempt_log) < attempt_limit:
                    attempt_number = len(attempt_log) + 1
                    try:
                        step_value = _execute_step(page, step, base_url)
                        attempt_log.append({"attempt": attempt_number, "result": "success", "error": None})
                        step_success = True
                        break
                    except Exception as exc:
                        attempt_log.append(
                            {"attempt": attempt_number, "result": "failed", "error": _stable_error(exc)}
                        )
                        if len(attempt_log) < attempt_limit:
                            if backoff_ms:
                                time.sleep(backoff_ms / 1000)
                            continue
                        if takeover_on_failure and action != "pause" and not taken_over:
                            takeover_events.extend(
                                run_control._handoff(
                                    "step_failed_after_retries",
                                    step,
                                    page,
                                    takeover_callback,
                                )
                            )
                            attempt_limit += 1
                            taken_over = True
                            if backoff_ms:
                                time.sleep(backoff_ms / 1000)
                        else:
                            break

                if action == "pause" and step_success:
                    message = step.get("message", "Review the live browser session, then resume the script.")
                    takeover_events.extend(
                        run_control._handoff(message, step, page, takeover_callback)
                    )
                try:
                    page.screenshot(path=str(screenshot_abs), full_page=True, animations="disabled")
                except PlaywrightError as exc:
                    if step_success:
                        step_success = False
                        attempt_log[-1]["result"] = "failed"
                        attempt_log[-1]["error"] = f"ScreenshotError: {type(exc).__name__}"

                if action == "extract" and step_success:
                    extracted[step_id] = step_value
                elapsed_ms = round((time.perf_counter() - step_start) * 1000, 3)
                history.append(
                    {
                        "step_id": step_id,
                        "action": action,
                        "selector": selector,
                        "timestamp": _timestamp(),
                        "result": "success" if step_success else "failed",
                        "value": step_value if action == "extract" and step_success else None,
                        "screenshot_path": screenshot_rel,
                        "attempts": len(attempt_log),
                        "attempt_log": attempt_log,
                        "takeover_events": takeover_events,
                    }
                )
                metrics.append({"step_id": step_id, "duration_ms": elapsed_ms})
                if not step_success:
                    run_status = "failed"
                    break
        finally:
            context.close()
            browser.close()

    completed_at = _timestamp()
    run_data = {
        "schema_version": 1,
        "honesty_statement": HONESTY_STATEMENT,
        "status": run_status,
        "started_at": started_at,
        "completed_at": completed_at,
        "task": {"name": task["name"], "source_file": task_source, "inputs": task.get("inputs", {}), "script": clean_task},
        "action_history": history,
        "step_metrics": metrics,
        "extracted_values": extracted,
    }
    run_path = out / "run.json"
    run_path.write_text(json.dumps(run_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    audit_file = Path(audit_path) if audit_path is not None else out / "bw-audit.jsonl"
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    audit_record = {
        "task_file": task_source,
        "task_name": task["name"],
        "task": {"inputs": task.get("inputs", {}), "script": clean_task},
        "started_at": started_at,
        "completed_at": completed_at,
        "status": run_status,
        "steps": history,
        "screenshots": [item["screenshot_path"] for item in history],
        "takeover_events": [event for item in history for event in item["takeover_events"]],
    }
    with audit_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(audit_record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return RunResult(out, run_status, history, metrics, extracted, run_data)


def _execute_step(page, step: dict[str, Any], base_url: str) -> Any:
    action = step["action"]
    selector = step.get("selector")
    timeout = step.get("timeout_ms", 5000)
    value = None
    try:
        if action == "goto":
            target = urljoin(base_url.rstrip("/") + "/", step["url"]) if base_url else step["url"]
            page.goto(target, wait_until="domcontentloaded", timeout=timeout)
        elif action == "click":
            page.locator(selector).click(timeout=timeout)
        elif action == "fill":
            page.locator(selector).fill(step["value"], timeout=timeout)
        elif action == "wait":
            if "duration_ms" in step:
                page.wait_for_timeout(step["duration_ms"])
            else:
                page.locator(selector).wait_for(state=step.get("state", "visible"), timeout=timeout)
        elif action == "extract":
            locator = page.locator(selector)
            if step.get("mode", "text") == "attribute":
                value = locator.get_attribute(step["attribute"], timeout=timeout)
            else:
                value = locator.text_content(timeout=timeout)
                value = value.strip() if value is not None else ""
        elif action == "screenshot":
            pass
        elif action == "pause":
            pass
    except PlaywrightTimeoutError as exc:
        raise StepExecutionError(f"Timed out during {action}") from exc
    except PlaywrightError as exc:
        raise StepExecutionError(f"Browser error during {action}: {type(exc).__name__}") from exc

    expected = step.get("expected_text")
    if expected is not None:
        if action == "extract":
            if expected not in str(value):
                raise StepExecutionError(f"Expected text was not in extracted value: {expected!r}")
        else:
            try:
                page.get_by_text(expected, exact=False).first.wait_for(state="visible", timeout=timeout)
            except PlaywrightTimeoutError as exc:
                raise StepExecutionError(f"Expected text was not visible: {expected!r}") from exc
    return value


def _stable_error(exc: Exception) -> str:
    if isinstance(exc, StepExecutionError):
        return f"StepExecutionError: {exc}"
    return f"{type(exc).__name__}: {str(exc).strip()}"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
