#!/usr/bin/env python3
"""Check PyPI storage usage for this project against the quota limits
documented at https://docs.pypi.org/project-management/storage-limits/.

Intended to run as a daily GitHub Actions job. Sets GitHub Actions outputs
when usage crosses the configured warning threshold, so the workflow can
post a Slack alert before publishes start failing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

PYPI_PROJECT_LIMIT_BYTES = 50 * 1024**3  # 50 GiB (increased from PyPI default 10 GiB)
PYPI_FILE_LIMIT_BYTES = 100 * 1024**2  # 100 MiB


def fetch_project_files(package: str) -> list[dict]:
    req = urllib.request.Request(
        f"https://pypi.org/simple/{package}/",
        headers={
            "Accept": "application/vnd.pypi.simple.v1+json",
            "User-Agent": "claude-agent-sdk-quota-check",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return data.get("files", [])


def human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TiB"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default="claude-agent-sdk")
    parser.add_argument(
        "--project-limit",
        type=int,
        default=PYPI_PROJECT_LIMIT_BYTES,
        help="Total project size limit in bytes (default: 50 GiB)",
    )
    parser.add_argument(
        "--file-limit",
        type=int,
        default=PYPI_FILE_LIMIT_BYTES,
        help="Per-file size limit in bytes (default: PyPI's 100 MiB)",
    )
    parser.add_argument(
        "--warn-threshold",
        type=float,
        default=0.80,
        help="Fraction of limit that triggers a warning (default: 0.80)",
    )
    args = parser.parse_args()

    files = fetch_project_files(args.package)
    total = sum(f.get("size", 0) for f in files)
    largest = max(files, key=lambda f: f.get("size", 0), default={})
    largest_size = largest.get("size", 0)
    largest_name = largest.get("filename", "<none>")

    project_pct = total / args.project_limit
    file_pct = largest_size / args.file_limit

    print(f"Package:        {args.package}")
    print(f"Files on PyPI:  {len(files)}")
    print(
        f"Project usage:  {human(total)} / {human(args.project_limit)} "
        f"({project_pct:.1%})"
    )
    print(
        f"Largest file:   {human(largest_size)} / {human(args.file_limit)} "
        f"({file_pct:.1%}) — {largest_name}"
    )

    over_project = project_pct >= args.warn_threshold
    over_file = file_pct >= args.warn_threshold
    alert = over_project or over_file

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        summary = (
            f"*PyPI quota warning for `{args.package}`*\n"
            f"• Project: {human(total)} / {human(args.project_limit)} "
            f"({project_pct:.1%})"
            f"{' :rotating_light:' if over_project else ''}\n"
            f"• Largest file: {human(largest_size)} / "
            f"{human(args.file_limit)} ({file_pct:.1%})"
            f"{' :rotating_light:' if over_file else ''}\n"
            f"Consider yanking old releases or requesting a limit increase "
            f"before the next publish."
        )
        with Path(gh_out).open("a", encoding="utf-8") as f:
            f.write(f"alert={'true' if alert else 'false'}\n")
            f.write(f"project_pct={project_pct:.3f}\n")
            f.write(f"file_pct={file_pct:.3f}\n")
            f.write("summary<<EOF\n")
            f.write(summary)
            f.write("\nEOF\n")

    if alert:
        which = []
        if over_project:
            which.append(f"project size at {project_pct:.1%} of limit")
        if over_file:
            which.append(f"largest file at {file_pct:.1%} of limit")
        print(f"::warning::PyPI quota threshold exceeded: {'; '.join(which)}")
    else:
        print("All quotas below warning threshold.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
