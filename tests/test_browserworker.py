from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from browserworker.report import generate_report
from browserworker.runner import RunControl, run_task
from browserworker.task import TaskValidationError, validate_task
from tests.helpers import sample_task


ROOT = Path(__file__).resolve().parents[1]


def _request(url: str) -> str:
    with urllib.request.urlopen(url, timeout=3) as response:
        return response.read().decode("utf-8")


def _write_task(path: Path, task: dict) -> Path:
    path.write_text(json.dumps(task, indent=2), encoding="utf-8")
    return path


def test_fixture_server_has_pages_and_flaky_endpoint_once(fixture_server):
    assert "Fixture sign in" in _request(f"{fixture_server.url}/login")
    assert "Fixture search" in _request(f"{fixture_server.url}/search")
    assert "result-title" in _request(f"{fixture_server.url}/results?q=browser")
    assert "flaky-button" in _request(f"{fixture_server.url}/flaky")
    with pytest.raises(urllib.error.HTTPError) as first:
        _request(f"{fixture_server.url}/api/flaky-click")
    assert first.value.code == 503
    assert '"ok": true' in _request(f"{fixture_server.url}/api/flaky-click")
    assert '"ok": true' in _request(f"{fixture_server.url}/api/flaky-click")


def test_validator_rejects_bad_step_by_name():
    task = {
        "name": "bad-task",
        "retry_policy": {"max_attempts": 1, "backoff_ms": 0},
        "steps": [{"id": "missing-selector", "action": "click"}],
    }
    with pytest.raises(TaskValidationError, match="step 'missing-selector'.*selector"):
        validate_task(task)


def test_runner_logs_login_extract_and_flaky_retry(fixture_server, tmp_path):
    result = run_task(sample_task(fixture_server.url), tmp_path / "run")
    assert result.status == "completed"
    assert result.extracted_values["extract-welcome"] == "Welcome, Henry"
    assert result.extracted_values["extract-result"] == "Deterministic Browser Tasks"
    assert result.extracted_values["extract-flaky-status"] == "Flaky action complete"
    flaky = next(entry for entry in result.action_history if entry["step_id"] == "retry-flaky-button")
    assert flaky["result"] == "success"
    assert flaky["attempts"] == 2
    assert [attempt["result"] for attempt in flaky["attempt_log"]] == ["failed", "success"]


def test_action_history_matches_steps_and_screenshots_exist(fixture_server, tmp_path):
    task = sample_task(fixture_server.url)
    result = run_task(task, tmp_path / "run")
    expected_ids = [step["id"] for step in task["steps"]]
    assert [entry["step_id"] for entry in result.action_history] == expected_ids
    assert len({entry["screenshot_path"] for entry in result.action_history}) == len(expected_ids)
    for entry in result.action_history:
        image = result.output_dir / entry["screenshot_path"]
        assert image.is_file()
        assert image.stat().st_size > 100
    saved = json.loads((result.output_dir / "run.json").read_text(encoding="utf-8"))
    assert saved["action_history"] == result.action_history
    audit = result.output_dir / "bw-audit.jsonl"
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 1


def test_pause_step_resumes_through_run_control_api(fixture_server, tmp_path):
    task = {
        "name": "pause-and-resume",
        "base_url": fixture_server.url,
        "retry_policy": {"max_attempts": 1, "backoff_ms": 0},
        "steps": [
            {"id": "open", "action": "goto", "url": "/login"},
            {"id": "handoff", "action": "pause", "message": "Review the sign-in page"},
            {"id": "read-title", "action": "extract", "selector": "h1"},
        ],
    }
    control = RunControl()
    result_holder = []
    error_holder = []

    def run():
        try:
            result_holder.append(run_task(task, tmp_path / "paused", control=control))
        except BaseException as exc:
            error_holder.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert control.wait_until_paused(timeout=15)
    control.resume()
    thread.join(timeout=20)
    assert not thread.is_alive()
    assert error_holder == []
    result = result_holder[0]
    assert result.status == "completed"
    pause_entry = next(entry for entry in result.action_history if entry["step_id"] == "handoff")
    assert [event["event"] for event in pause_entry["takeover_events"]] == [
        "takeover_paused",
        "takeover_resumed",
    ]


def test_takeover_on_failure_retries_the_same_step(fixture_server, tmp_path):
    task = {
        "name": "failure-handoff",
        "base_url": fixture_server.url,
        "retry_policy": {"max_attempts": 1, "backoff_ms": 0},
        "steps": [
            {"id": "open", "action": "goto", "url": "/login"},
            {
                "id": "manual-action",
                "action": "click",
                "selector": "#manual-action",
                "timeout_ms": 250,
                "expected_text": "Manual fix complete",
            },
        ],
    }

    def operator_resumes(gate, page, step, reason):
        assert step["id"] == "manual-action"
        assert reason == "step_failed_after_retries"
        page.evaluate("""() => {
          const button = document.createElement('button');
          button.id = 'manual-action';
          button.textContent = 'Complete manual fix';
          button.onclick = () => { document.body.insertAdjacentHTML('beforeend', '<p>Manual fix complete</p>'); };
          document.body.append(button);
        }""")
        gate.resume()

    result = run_task(
        task,
        tmp_path / "failure-handoff",
        takeover_on_failure=True,
        takeover_callback=operator_resumes,
    )
    step = result.action_history[-1]
    assert result.status == "completed"
    assert step["attempts"] == 2
    assert [attempt["result"] for attempt in step["attempt_log"]] == ["failed", "success"]
    assert [event["event"] for event in step["takeover_events"]] == ["takeover_paused", "takeover_resumed"]


def test_identical_tasks_have_identical_outcomes_and_values(fixture_server, tmp_path):
    task = sample_task(fixture_server.url)
    first = run_task(task, tmp_path / "first")
    second = run_task(task, tmp_path / "second")

    def without_timestamps(history):
        copied = json.loads(json.dumps(history))
        for entry in copied:
            entry.pop("timestamp")
            for event in entry["takeover_events"]:
                event.pop("timestamp")
        return copied

    assert without_timestamps(first.action_history) == without_timestamps(second.action_history)
    assert first.extracted_values == second.extracted_values


def test_html_report_contains_steps_banner_and_embedded_screenshots(fixture_server, tmp_path):
    task = sample_task(fixture_server.url)
    result = run_task(task, tmp_path / "report-run")
    report = generate_report(result.output_dir, "html")
    contents = report.read_text(encoding="utf-8")
    for step in task["steps"]:
        assert step["id"] in contents
    assert "explicit, human-written scripts" in contents
    assert "human-takeover support" in contents
    assert "data:image/png;base64," in contents
    markdown = generate_report(result.output_dir, "md").read_text(encoding="utf-8")
    assert "![open-login screenshot](screenshots/001-open-login.png)" in markdown
    assert "Duration (ms)" in markdown


def test_cli_validate_run_and_report_smoke(fixture_server, tmp_path):
    task_path = _write_task(tmp_path / "task.json", sample_task(fixture_server.url))
    validated = subprocess.run(
        [sys.executable, "-m", "browserworker", "validate", str(task_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert validated.returncode == 0, validated.stderr
    assert "Valid task" in validated.stdout
    assert "human takeover" in validated.stdout
    help_text = subprocess.run(
        [sys.executable, "-m", "browserworker", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert help_text.returncode == 0
    assert "does not decide what to do" in help_text.stdout
    assert "human takeover" in help_text.stdout

    out_dir = tmp_path / "cli-run"
    audit_path = tmp_path / "cli-audit.jsonl"
    ran = subprocess.run(
        [
            sys.executable,
            "-m",
            "browserworker",
            "run",
            str(task_path),
            "--out",
            str(out_dir),
            "--audit",
            str(audit_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert ran.returncode == 0, ran.stderr + ran.stdout
    assert (out_dir / "run.json").is_file()
    assert audit_path.is_file()

    reported = subprocess.run(
        [sys.executable, "-m", "browserworker", "report", str(out_dir), "--format", "html"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert reported.returncode == 0, reported.stderr
    report = out_dir / "report.html"
    assert report.is_file()
    assert "not autonomous AI browsing" in report.read_text(encoding="utf-8")


def test_cli_fixtures_subcommand_serves_health(free_port):
    process = subprocess.Popen(
        [sys.executable, "-m", "browserworker", "fixtures", "--serve", "--port", str(free_port)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        line = process.stdout.readline()
        assert f"127.0.0.1:{free_port}" in line
        assert '"ok": true' in _request(f"http://127.0.0.1:{free_port}/health")
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def free_port():
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
