#!/usr/bin/env python3
"""Strip third-party personal data out of the BLE research logs.

    ./.venv/bin/python tools/sanitize_ble_logs.py --check     # report only
    ./.venv/bin/python tools/sanitize_ble_logs.py --write     # rewrite in place

A Bluetooth Low Energy scan records every advertising device in range, not only the one
being researched. These logs captured 193 distinct devices belonging to neighbours,
including people's names, a named fitness tracker, and a CPAP machine with a serial number
-- which reveals a third party's medical condition. None of that is the owner's to publish,
and none of it has any research value: the investigation concerned one Fitbit Air.

So the rule is subtractive, not cosmetic. Records for any device other than the Fitbit Air
are **removed**, not anonymised, because an anonymised record still carries manufacturer
payloads, signal strengths and timing that fingerprint a device and a household. The Fitbit
Air's own per-host CoreBluetooth identifiers are replaced with a fixed placeholder.

Every conclusion in FITBIT_AIR_RESEARCH.md rests on the Fitbit's own records, so the
research survives this intact.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The Fitbit Air as seen by CoreBluetooth. macOS issues a per-host peripheral UUID rather
# than a hardware address, and it differed between sessions, so both are listed.
FITBIT_ADDRESSES = {
    "FITBIT-AIR-LOCAL-HANDLE",
    "FITBIT-AIR-LOCAL-HANDLE",
    "FITBIT-AIR-LOCAL-HANDLE",
}
FITBIT_PLACEHOLDER = "FITBIT-AIR-LOCAL-HANDLE"
FITBIT_NAMES = {"Google Fitbit Air"}

# Absolute home paths leak the machine's account name through every stack trace and
# "saved to ..." line in the console transcripts.
HOME_PATH_RE = re.compile(r"/Users/<user>/\s\"']+")
HOME_PLACEHOLDER = "/Users/<user>"

# Any 36-character UUID that looks like a CoreBluetooth peripheral handle.
ADDRESS_RE = re.compile(r"\b[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\b")
# Service and characteristic UUIDs are product-level identifiers, not personal ones, and
# they are the actual findings. They are lowercase in these files, so the uppercase-only
# pattern above already excludes them; this is the explicit guard.
KEEP_PREFIXES = ("0000", "abba", "adab", "8e40", "fd62")

TARGET_DIRS = ("logs", "captures")


def _is_fitbit_address(value: str) -> bool:
    """True for the Fitbit's own handles, and for an already-substituted placeholder.

    The placeholder must count as ours or the script is not idempotent: a second run would
    fail to recognise its own output and delete the very records it was written to keep.
    """
    return value.upper() in FITBIT_ADDRESSES or value == FITBIT_PLACEHOLDER


def _scrub_paths(text: str) -> str:
    return HOME_PATH_RE.sub(HOME_PLACEHOLDER, text)


def _scrub_addresses(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        value = match.group(0)
        if value.lower().startswith(KEEP_PREFIXES):
            return value
        return FITBIT_PLACEHOLDER if _is_fitbit_address(value) else "REDACTED-DEVICE"

    return _scrub_paths(ADDRESS_RE.sub(repl, text))


def _record_is_ours(record: dict) -> bool:
    """True when a record describes the Fitbit Air or carries no device identity at all."""
    address = record.get("addr") or record.get("address")
    name = record.get("name")
    if address is None and name is None:
        # Connection-level events (notify, subscribed, connected) belong to the session
        # with the Fitbit; they identify no other device.
        return True
    if address is not None and _is_fitbit_address(str(address)):
        return True
    if name is not None and str(name) in FITBIT_NAMES:
        return True
    return False


def sanitize_jsonl(path: Path) -> tuple[str, int, int]:
    kept: list[str] = []
    dropped = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            kept.append(_scrub_addresses(line))
            continue
        if isinstance(record, dict) and not _record_is_ours(record):
            dropped += 1
            continue
        kept.append(_scrub_addresses(json.dumps(record)))
    return "\n".join(kept) + ("\n" if kept else ""), len(kept), dropped


def sanitize_json(path: Path) -> tuple[str, int, int]:
    payload = json.loads(path.read_text())
    if isinstance(payload, list):
        keep = [r for r in payload if not isinstance(r, dict) or _record_is_ours(r)]
        dropped = len(payload) - len(keep)
        return _scrub_addresses(json.dumps(keep, indent=2)) + "\n", len(keep), dropped
    if isinstance(payload, dict):
        payload.pop("name", None) if payload.get("name") in (None,) else None
        return _scrub_addresses(json.dumps(payload, indent=2)) + "\n", 1, 0
    return _scrub_addresses(json.dumps(payload, indent=2)) + "\n", 1, 0


def sanitize_text(path: Path) -> tuple[str, int, int]:
    """Console transcripts: drop any line naming a device that is not the Fitbit."""
    kept: list[str] = []
    dropped = 0
    for line in path.read_text().splitlines():
        addresses = ADDRESS_RE.findall(line)
        foreign = [a for a in addresses if not _is_fitbit_address(a)]
        has_name = re.search(r"name=|'[^']*'|\"[^\"]*\"", line) is not None
        if foreign and has_name:
            dropped += 1
            continue
        kept.append(_scrub_addresses(line))
    return "\n".join(kept) + ("\n" if kept else ""), len(kept), dropped


def sanitize(path: Path) -> tuple[str, int, int]:
    if path.suffix == ".jsonl":
        return sanitize_jsonl(path)
    if path.suffix == ".json":
        return sanitize_json(path)
    return sanitize_text(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="rewrite files in place")
    parser.add_argument("--root", type=Path, default=REPO)
    args = parser.parse_args()

    total_dropped = 0
    changed = 0
    for directory in TARGET_DIRS:
        base = args.root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            if path.suffix not in (".json", ".jsonl", ".txt"):
                continue
            original = path.read_text()
            cleaned, kept, dropped = sanitize(path)
            if cleaned != original:
                changed += 1
                total_dropped += dropped
                print(f"{path.relative_to(args.root)}: kept {kept}, removed {dropped}")
                if args.write:
                    path.write_text(cleaned)
    print(f"\n{changed} file(s) {'rewritten' if args.write else 'would change'}; "
          f"{total_dropped} third-party record(s) removed")
    if not args.write:
        print("dry run -- pass --write to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
