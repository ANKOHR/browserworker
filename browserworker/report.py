"""Markdown and HTML run reports generated from actual run data."""

from __future__ import annotations

import base64
import html
import json
from pathlib import Path
from typing import Any

from .runner import HONESTY_STATEMENT


def generate_report(run_dir: str | Path, format: str = "md", output: str | Path | None = None) -> Path:
    """Render a report from a completed run directory."""
    if format not in {"md", "html"}:
        raise ValueError("Report format must be 'md' or 'html'")
    root = Path(run_dir)
    data_path = root / "run.json"
    if not data_path.is_file():
        raise FileNotFoundError(f"Run data not found: {data_path}")
    run_data = json.loads(data_path.read_text(encoding="utf-8"))
    destination = Path(output) if output else root / f"report.{format}"
    if not destination.is_absolute():
        destination = Path.cwd() / destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    if format == "md":
        content = _markdown(run_data)
    else:
        content = _html_report(root, run_data)
    destination.write_text(content, encoding="utf-8", newline="\n")
    return destination


def _markdown(data: dict[str, Any]) -> str:
    lines = [
        f"# BrowserWorker run: {data['task']['name']}",
        "",
        f"> {HONESTY_STATEMENT}",
        "",
        f"Status: **{data['status']}**",
        "",
        "## Inputs",
        "",
        "```json",
        json.dumps(data["task"], indent=2, ensure_ascii=False),
        "```",
        "",
        "## Steps",
        "",
        "| Step | Action | Status | Duration (ms) | Attempts | Screenshot |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    metric_by_id = {item["step_id"]: item["duration_ms"] for item in data["step_metrics"]}
    for entry in data["action_history"]:
        lines.append(
            f"| {entry['step_id']} | {entry['action']} | {entry['result']} | "
            f"{metric_by_id.get(entry['step_id'], 0)} | {entry['attempts']} | "
            f"![{entry['step_id']} screenshot]({entry['screenshot_path']}) |"
        )
    lines.extend(["", "## Action history", "", "```json", json.dumps(data["action_history"], indent=2, ensure_ascii=False), "```", ""])
    return "\n".join(lines)


def _html_report(root: Path, data: dict[str, Any]) -> str:
    metric_by_id = {item["step_id"]: item["duration_ms"] for item in data["step_metrics"]}
    rows: list[str] = []
    for entry in data["action_history"]:
        screenshot_path = root / entry["screenshot_path"]
        image_markup = ""
        if screenshot_path.is_file():
            encoded = base64.b64encode(screenshot_path.read_bytes()).decode("ascii")
            image_markup = f'<img alt="Screenshot for {html.escape(entry["step_id"])}" src="data:image/png;base64,{encoded}">' 
        rows.append(
            "<tr>"
            f"<td>{html.escape(entry['step_id'])}</td>"
            f"<td>{html.escape(entry['action'])}</td>"
            f"<td>{html.escape(entry['result'])}</td>"
            f"<td>{metric_by_id.get(entry['step_id'], 0)}</td>"
            f"<td>{entry['attempts']}</td>"
            f"<td>{image_markup}</td>"
            "</tr>"
        )
    history_json = html.escape(json.dumps(data["action_history"], indent=2, ensure_ascii=False))
    inputs_json = html.escape(json.dumps(data["task"], indent=2, ensure_ascii=False))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>BrowserWorker run: {html.escape(data['task']['name'])}</title>
<style>
body {{ font: 16px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 1100px; padding: 0 1rem; color: #17212b; }}
.honesty {{ padding: 1rem; background: #fff4d6; border: 1px solid #d79e00; font-weight: 650; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ border-bottom: 1px solid #ccd3d9; padding: .65rem; text-align: left; vertical-align: top; }}
img {{ display: block; max-width: 360px; height: auto; border: 1px solid #ccd3d9; }}
pre {{ overflow: auto; padding: 1rem; background: #f2f5f7; }}
</style></head><body>
<header><h1>BrowserWorker run: {html.escape(data['task']['name'])}</h1>
<p class="honesty">{html.escape(HONESTY_STATEMENT)} Runs are scripted automation with human-takeover support, not autonomous AI browsing.</p>
<p>Run status: <strong>{html.escape(data['status'])}</strong></p></header>
<h2>Inputs</h2><pre>{inputs_json}</pre>
<h2>Steps and screenshots</h2><table><thead><tr><th>Step</th><th>Action</th><th>Status</th><th>Duration (ms)</th><th>Attempts</th><th>Screenshot</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h2>Full action history</h2><pre>{history_json}</pre>
</body></html>
"""
