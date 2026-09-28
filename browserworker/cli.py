"""Command-line interface for BrowserWorker."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .fixtures import serve
from .report import generate_report
from .runner import HONESTY_STATEMENT, RunControl, run_task
from .task import TaskValidationError, load_task


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="browserworker",
        description=(
            "BrowserWorker executes explicit human-written browser scripts with deterministic retries "
            "and human takeover. It does not decide what to do or understand pages."
        ),
        epilog=HONESTY_STATEMENT,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Run a validated task in real Chromium", epilog=HONESTY_STATEMENT
    )
    run_parser.add_argument("task", help="Path to a JSON task script")
    run_parser.add_argument("--out", required=True, help="Directory for screenshots and run data")
    run_parser.add_argument("--headed", action="store_true", help="Show Chromium for manual takeover")
    run_parser.add_argument(
        "--takeover-on-failure",
        action="store_true",
        help="Pause for a human after a step uses its declared retry attempts",
    )
    run_parser.add_argument("--audit", help="Append audit records to this JSON Lines file")

    validate_parser = subparsers.add_parser(
        "validate", help="Check a task script without launching a browser", epilog=HONESTY_STATEMENT
    )
    validate_parser.add_argument("task", help="Path to a JSON task script")

    fixture_parser = subparsers.add_parser(
        "fixtures", help="Serve the local multi-page fixture site", epilog=HONESTY_STATEMENT
    )
    fixture_parser.add_argument("--serve", action="store_true", help="Start the fixture HTTP server")
    fixture_parser.add_argument("--host", default="127.0.0.1", help="Bind address, default 127.0.0.1")
    fixture_parser.add_argument("--port", type=int, default=8765, help="Port, default 8765, use 0 for a free port")

    report_parser = subparsers.add_parser(
        "report", help="Build an HTML or Markdown report from a run", epilog=HONESTY_STATEMENT
    )
    report_parser.add_argument("run_dir", help="Directory containing run.json and screenshots")
    report_parser.add_argument("--format", choices=("md", "html"), default="md")
    report_parser.add_argument("--output", help="Report destination, defaults to run-dir/report.<format>")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            task = load_task(args.task)
            print(f"Valid task '{task['name']}': {len(task['steps'])} steps.")
            print(HONESTY_STATEMENT)
            return 0
        if args.command == "fixtures":
            if not args.serve:
                parser.error("fixtures requires --serve")
            serve(args.host, args.port)
            return 0
        if args.command == "run":
            task = load_task(args.task)
            control = RunControl()

            def terminal_takeover(gate, page, step, reason):
                print("\nHuman takeover requested.", flush=True)
                print(f"Reason: {reason}", flush=True)
                print(f"Current step: {step['id']}", flush=True)
                print(f"Browser page: {page.url}", flush=True)
                print("Complete any manual browser work, then press Enter to resume this step.", flush=True)
                try:
                    input()
                except EOFError:
                    print("No terminal input is available. The run remains paused.", flush=True)
                    return
                gate.resume()

            result = run_task(
                task,
                args.out,
                headed=args.headed,
                takeover_on_failure=args.takeover_on_failure,
                control=control,
                takeover_callback=terminal_takeover,
                audit_path=args.audit or Path.cwd() / "bw-audit.jsonl",
            )
            print(f"Run status: {result.status}")
            print(f"Run data: {result.output_dir / 'run.json'}")
            print(f"Screenshots: {result.output_dir / 'screenshots'}")
            print(HONESTY_STATEMENT)
            return 0 if result.status == "completed" else 1
        if args.command == "report":
            report_path = generate_report(args.run_dir, args.format, args.output)
            print(f"Report written: {report_path}")
            print(HONESTY_STATEMENT)
            return 0
    except (TaskValidationError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
