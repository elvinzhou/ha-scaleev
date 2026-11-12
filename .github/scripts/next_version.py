#!/usr/bin/env python3
"""Work out the next release version from conventional-commit messages.

fix:/perf: -> patch, feat: -> minor, `!` or a BREAKING CHANGE footer -> major
(minor while below 1.0). Other types (docs, test, ci, chore, ...) don't release.

Usage: next_version.py [LAST_TAG]   # prints the new version, or nothing
"""
import re
import subprocess
import sys

TYPE_RE = re.compile(r"^(?P<type>[a-z]+)(\([^)]*\))?(?P<bang>!)?:\s")
ORDER = {"patch": 1, "minor": 2, "major": 3}


def bump_for(subject: str, body: str = "") -> str | None:
    m = TYPE_RE.match(subject)
    if not m:
        return None
    if m["bang"] or re.search(r"^BREAKING[ -]CHANGE:", body, re.M):
        return "major"
    return {"feat": "minor", "fix": "patch", "perf": "patch"}.get(m["type"])


def next_version(current: str, bumps: list[str | None]) -> str | None:
    wanted = [b for b in bumps if b]
    if not wanted:
        return None
    bump = max(wanted, key=ORDER.__getitem__)
    major, minor, patch = (int(x) for x in current.split("."))
    if bump == "major" and major == 0:
        bump = "minor"
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def main(last_tag: str | None) -> None:
    current = last_tag.removeprefix("v") if last_tag else "0.0.0"
    rng = [f"{last_tag}..HEAD"] if last_tag else []
    log = subprocess.run(
        ["git", "log", *rng, "--format=%s%x1f%b%x1e"], capture_output=True, text=True, check=True
    ).stdout
    bumps = []
    for entry in filter(None, (e.strip("\n") for e in log.split("\x1e"))):
        subject, _, body = entry.partition("\x1f")
        bumps.append(bump_for(subject.strip(), body))
    version = next_version(current, bumps)
    if version:
        print(version)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None)
