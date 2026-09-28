# BrowserWorker

BrowserWorker runs explicit browser task scripts in real Chromium. It records step outcomes, deterministic retries, screenshots, and human takeover events so repetitive web workflows can be reviewed and replayed.

## Quickstart

Python 3.10 or newer is required. Install the package and its test tools, then install Playwright Chromium:

```powershell
python -m pip install -e ".[test]"
python -m playwright install chromium
```

Start the local fixture site in one terminal:

```powershell
python -m browserworker fixtures --serve
```

In another terminal, validate and run the example task, then create the HTML report:

```powershell
python -m browserworker validate examples/demo-task.json
python -m browserworker run examples/demo-task.json --out runs/demo
python -m browserworker report runs/demo --format html
```

The demo visits only `127.0.0.1:8765`. It signs in to a fixture page, searches for a fixed term, extracts fixture values, retries a button that fails its first click, and stores a screenshot for each step. Run `python -m pytest` to run the suite. Tests use only the local fixture server.

## Honesty boundary

BrowserWorker executes explicit, human-written scripts. It does not decide what to do, understand pages, or browse on its own. It is scripted automation with deterministic retries and human takeover. Each browser action comes from the JSON task, and extracted values are limited to the selectors and attributes the script names.

The CLI help and generated reports state this boundary. The HTML report labels runs as scripted automation with human-takeover support. A person authors each task and reviews any handoff. BrowserWorker does not claim that a model selected its steps.

## Architecture

```text
JSON task
   |
   v
validate (schema checks, no browser)
   |
   v
runner --> Playwright --> Chromium --> local or selected site
   |            |             |
   |            |             +--> one PNG screenshot per step
   |            +--> fixed retry policy and pause/resume control
   +--> run.json and append-only bw-audit.jsonl
                         |
                         v
                   md or HTML report
```

The fixture service uses Python's standard library. The runner uses a fixed viewport, locale, timezone, and browser preferences. Chromium is launched with GPU acceleration disabled. No random choices are made, so identical task inputs and fixture state produce identical step outcomes, extracted values, and non-time history fields. Timestamps and measured durations are expected to vary. No random seed is used because the runner has no random behavior.

## Task script reference

A task is a JSON object with a non-empty `name`, a `retry_policy`, a non-empty `steps` array, and optional `base_url` and object `inputs`. Step IDs must be unique. Unknown fields are rejected. Both retry policy values must be declared so the execution policy is visible in the script.

```json
{
  "name": "search-example",
  "base_url": "http://127.0.0.1:8765",
  "inputs": {"query": "browser"},
  "retry_policy": {"max_attempts": 2, "backoff_ms": 20},
  "steps": [
    {"id": "open-search", "action": "goto", "url": "/search"},
    {"id": "fill-query", "action": "fill", "selector": "#query", "value": "browser"},
    {"id": "submit", "action": "click", "selector": "#search-submit", "expected_text": "Results for"},
    {"id": "read-result", "action": "extract", "selector": ".result-title", "mode": "text"}
  ]
}
```

Supported actions:

| Action | Required fields | Optional fields | Behavior |
| --- | --- | --- | --- |
| `goto` | `url` | `timeout_ms`, `expected_text` | Navigate to the URL, joined to `base_url` when relative. |
| `click` | `selector` | `timeout_ms`, `expected_text` | Click the first matching CSS selector. |
| `fill` | `selector`, `value` | `timeout_ms`, `expected_text` | Fill a text input or textarea. |
| `wait` | Exactly one of `duration_ms` or `selector` | `timeout_ms`, `state`, `expected_text` | Wait a fixed duration or for a selector to become `visible` or `hidden`. |
| `screenshot` | None | `timeout_ms`, `expected_text` | Capture the current page. Every step already receives a screenshot, so this action can mark a visual checkpoint in the script. |
| `extract` | `selector` | `mode`, `attribute`, `timeout_ms`, `expected_text` | Read text content, or an attribute when `mode` is `attribute`. |
| `pause` | None | `message`, `timeout_ms` | Keep the live page open until a human signals resume. |

`timeout_ms` defaults to 5000. `expected_text` checks that the text is visible after an action. For `extract`, it checks that the extracted value contains the text. `retry_policy.max_attempts` is the total number of tries per step, including the first try. `backoff_ms` is a fixed delay between tries. Declare `{"max_attempts": 1, "backoff_ms": 0}` to disable retries. Every try is recorded.

## Human takeover

Use `pause` in the task to hand the live browser to an operator. From Python, pass a `RunControl` to `run_task`, wait for `control.wait_until_paused()`, work in the browser, and call `control.resume()`. In the CLI, use `--headed` to show the browser. `--takeover-on-failure` pauses after the declared retry attempts and retries that same step once after the operator resumes. The pause and resume timestamps are stored with the associated step.

## CLI

```text
browserworker validate <task.json>
browserworker run <task.json> --out <directory> [--headed] [--takeover-on-failure] [--audit <file>]
browserworker fixtures --serve [--host <address>] [--port <port>]
browserworker report <run-directory> [--format md|html] [--output <file>]
```

The default audit file is `bw-audit.jsonl` in the current directory. Every run appends one JSON record. Run data and screenshots are stored under the output directory. Reports are generated from that run data and embed its real screenshots in HTML output.

## Local fixture pages

The standard-library fixture server provides a login form, welcome page, search form, results table, and flaky button. The flaky endpoint fails its first click after each visit to `/flaky` and succeeds on the next. It is intended for local development and tests, not as a production web service.
