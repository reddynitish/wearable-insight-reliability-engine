"""Release gates that are cheap to check and expensive to get wrong."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def tracked() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [p for p in result.stdout.split("\0") if p]


def test_no_secrets_or_personal_data_are_tracked():
    """The same scan CI runs, so a failure here is a failure there."""
    result = subprocess.run(
        ["python", "tools/secret_scan.py"], cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "path",
    [
        "google_health/credentials.json",
        "google_health/token.json",
    ],
)
def test_credential_files_are_never_tracked(path):
    assert path not in tracked()


def test_no_personal_health_export_is_tracked():
    assert not [p for p in tracked() if p.startswith("google_health/data/")]


def test_the_engine_does_not_import_the_ble_research_scripts():
    """PROJECT_BRIEF.md section 14: engine code stays separate from capture artifacts."""
    for path in (REPO / "engine").rglob("*.py"):
        source = path.read_text()
        for forbidden in ("import bleak", "from bleak", "import scripts", "from scripts"):
            assert forbidden not in source, f"{path.name} reaches into the BLE research"


def test_the_engine_makes_no_network_calls():
    """A decision must be reproducible offline; a network call would break that."""
    import ast

    for path in (REPO / "engine").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name not in {"requests", "httpx", "urllib", "urllib3", "socket"}, (
                    f"{path.relative_to(REPO)} imports {name}"
                )


def test_the_required_documentation_exists():
    for path in (
        "README.md",
        "PROJECT_BRIEF.md",
        "FITBIT_AIR_RESEARCH.md",
        "docs/claim-contracts.md",
        "docs/evaluation-protocol.md",
        "docs/related-work.md",
        "docs/model-card.md",
        "docs/data-card.md",
        "docs/limitations.md",
        "docs/architecture.md",
        "docs/resume-positioning.md",
        "docs/model-card.md",
        "data/DATASETS.md",
        "docs/decisions/README.md",
    ):
        assert (REPO / path).is_file(), f"missing {path}"


def test_unverified_datasets_are_still_marked_unverified():
    """No dataset may be cited as fact before its licence and citation are confirmed.

    PMData is now verified against its primary source and in use, so the blanket
    "nothing downloaded" statement is gone. The remaining rows must still carry the mark.
    """
    text = (REPO / "data" / "DATASETS.md").read_text()
    assert "UNVERIFIED" in text
    assert "Remaining candidates (unverified, not downloaded)" in text


def test_the_verified_dataset_records_its_licence_and_citation():
    text = (REPO / "data" / "DATASETS.md").read_text()
    assert "CC BY 4.0" in text
    assert "10.1145/3339825.3394926" in text
    assert "VERIFIED" in text


def test_no_dataset_files_are_committed():
    assert not [p for p in tracked() if p.startswith("data/datasets/")]
    assert not [p for p in tracked() if p.endswith((".parquet", ".h5", ".pkl"))]


# --------------------------------------------------------------------------- privacy


def test_no_third_party_device_data_is_tracked():
    """A BLE scan records every device in range, not only the one being researched.

    These logs once carried 193 nearby devices including neighbours' names and a CPAP
    serial number. The scanner now fails the build if any of that reappears.
    """
    result = subprocess.run(
        ["python", "tools/secret_scan.py"], cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_ble_log_sanitiser_is_idempotent():
    """Running it twice must not remove anything the first run kept.

    The first version of the sanitiser failed this: it did not recognise its own
    placeholder, so a second run began deleting the Fitbit records it existed to preserve.
    """
    result = subprocess.run(
        ["python", "tools/sanitize_ble_logs.py"], cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout
    assert "0 file(s) would change" in result.stdout, result.stdout


def test_no_absolute_home_paths_are_tracked():
    """They leak the machine's account name through every stack trace."""
    import re

    pattern = re.compile(r"/Users/(?!<user>)[A-Za-z0-9._-]+")
    offenders = []
    for path in tracked():
        full = REPO / path
        if full.suffix.lower() not in {".py", ".md", ".txt", ".json", ".jsonl", ".yml", ".html"}:
            continue
        if path in {"tools/secret_scan.py", "tools/sanitize_ble_logs.py"}:
            continue
        try:
            if pattern.search(full.read_text(errors="replace")):
                offenders.append(path)
        except OSError:
            continue
    assert not offenders, offenders
