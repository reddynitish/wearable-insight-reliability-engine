#!/usr/bin/env python3
"""Scan the git index for secrets and personal health data before publishing.

    ./.venv/bin/python tools/secret_scan.py

Checks only files git is actually tracking, because an ignored file on disk is not the
risk -- a tracked one is. Exits non-zero on any finding, so it works as a CI gate and as a
pre-publish check (PROJECT_BRIEF.md section 19).

It never prints a matched secret, only the file, line number, and which rule fired.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Paths that must never be tracked at all.
FORBIDDEN_PATHS = (
    re.compile(r"(^|/)credentials\.json$"),
    re.compile(r"(^|/)token\.json$"),
    re.compile(r"^google_health/data/"),
    re.compile(r"^data/datasets/"),
    re.compile(r"^data/personal/"),
    re.compile(r"\.pem$"),
    re.compile(r"\.p12$"),
    re.compile(r"(^|/)\.env$"),
)

# Content patterns. Each is (rule name, pattern). Kept narrow: a rule that fires on every
# occurrence of the word "token" gets ignored, and an ignored scanner protects nothing.
CONTENT_RULES = (
    ("google oauth client id", re.compile(r"\d{10,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com")),
    ("google api key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("google oauth client secret", re.compile(r"GOCSPX-[0-9A-Za-z_\-]{20,}")),
    ("google oauth refresh token", re.compile(r"\b1//[0-9A-Za-z_\-]{30,}")),
    ("bearer token literal", re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{25,}")),
    ("github token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,})\b")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("assigned secret literal", re.compile(
        r"""(?ix)\b(client_secret|api_key|apikey|access_token|refresh_token|password|passwd)\b
            \s*[:=]\s*
            ['"][^'"\s{}$<>]{12,}['"]"""
    )),
    ("email address", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
)

# Files where a pattern is legitimate: the scanner's own rules, and docs that name the
# files to avoid. Each entry is (path prefix, rule name) or (path prefix, "*").
ALLOWLIST = (
    ("tools/secret_scan.py", "*"),
    ("google_health/README.md", "email address"),
    ("tests/test_schemas.py", "email address"),
    ("tests/test_api.py", "email address"),
    ("docs/", "email address"),
    ("PROJECT_BRIEF.md", "email address"),
    ("README.md", "email address"),
)

TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".html", ".js", ".css", ".sh", ".dockerfile", "",
}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [p for p in result.stdout.split("\0") if p]


def allowed(path: str, rule: str) -> bool:
    return any(
        path.startswith(prefix) and (allowed_rule in ("*", rule))
        for prefix, allowed_rule in ALLOWLIST
    )


def main() -> int:
    findings: list[str] = []
    files = tracked_files()

    for path in files:
        for pattern in FORBIDDEN_PATHS:
            if pattern.search(path):
                findings.append(f"{path}: tracked but must never be committed")

    for path in files:
        full = REPO / path
        if full.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = full.read_text(errors="replace")
        except OSError:
            continue
        for rule, pattern in CONTENT_RULES:
            if allowed(path, rule):
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    findings.append(f"{path}:{number}: matched rule {rule!r}")

    print(f"scanned {len(files)} tracked file(s)")
    if findings:
        print(f"\n{len(findings)} finding(s):")
        for finding in sorted(set(findings)):
            print(f"  {finding}")
        print("\nNothing above is printed with its matched value. Inspect the file yourself.")
        return 1
    print("no secrets or personal health data found in tracked files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
