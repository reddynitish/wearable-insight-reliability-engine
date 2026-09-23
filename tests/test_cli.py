"""The CLI is the offline demo surface; it must work without a server or a network."""
from __future__ import annotations

import json

import pytest
from conftest import rhr_request

from engine.cli import main


def test_demo_runs_every_claim_type_and_labels_the_evidence_synthetic(capsys):
    assert main(["--no-color", "demo"]) == 0
    out = capsys.readouterr().out
    assert "synthetic" in out
    for decision in ("SHOW", "SHOW_WITH_WARNING", "WAIT_FOR_MORE_DATA", "REJECT"):
        assert decision in out
    for claim_type in ("RESTING_HEART_RATE_ELEVATED", "SLEEP_DURATION_LOW",
                       "RECOVERY_EVIDENCE_INCOMPLETE"):
        assert claim_type in out


def test_demo_reports_the_ground_truth_beside_each_decision(capsys):
    main(["--no-color", "demo", "--claim", "RESTING_HEART_RATE_ELEVATED"])
    out = capsys.readouterr().out
    assert "ground truth SUPPORTABLE" in out
    assert "ground truth UNSUPPORTABLE" in out


def test_corrupt_shows_before_and_after(capsys):
    assert main([
        "--no-color", "corrupt", "--claim", "RESTING_HEART_RATE_ELEVATED",
        "--corruption", "truncate_baseline", "--severity", "severe",
    ]) == 0
    out = capsys.readouterr().out
    assert "before (clean evidence)" in out
    assert "decision   SHOW" in out
    assert "WAIT_FOR_MORE_DATA" in out
    assert "breaches" in out


def test_evaluate_reads_a_request_from_a_file(tmp_path, capsys):
    path = tmp_path / "request.json"
    path.write_text(rhr_request().model_dump_json())
    assert main(["--no-color", "evaluate", str(path)]) == 0
    assert "SHOW" in capsys.readouterr().out


def test_evaluate_can_emit_machine_readable_json(tmp_path, capsys):
    path = tmp_path / "request.json"
    path.write_text(rhr_request(n_baseline=3).model_dump_json())
    main(["--no-color", "evaluate", str(path), "--json"])
    body = json.loads(capsys.readouterr().out)
    assert body["decision"] == "WAIT_FOR_MORE_DATA"
    assert body["support_is_calibrated"] is False
    assert body["trace"]["gates"]


def test_policies_prints_the_contract(capsys):
    assert main(["--no-color", "policies"]) == 0
    out = capsys.readouterr().out
    assert "RESTING_HEART_RATE_ELEVATED" in out
    assert "14 days required" in out
    assert "maximum decision: SHOW_WITH_WARNING" in out  # the two capped claims


def test_reasons_and_corruptions_catalogues_print(capsys):
    assert main(["--no-color", "reasons"]) == 0
    reasons = capsys.readouterr().out
    assert "BASELINE_NOT_MATURE" in reasons
    assert "forces=wait" in reasons

    assert main(["--no-color", "corruptions"]) == 0
    corruptions = capsys.readouterr().out
    assert "shorten_wear" in corruptions
    assert "benign; engine must absorb it" in corruptions


def test_verbose_prints_the_trace(capsys):
    main(["--no-color", "-v", "corrupt", "--corruption", "shorten_wear", "--severity", "severe"])
    out = capsys.readouterr().out
    assert '"gates"' in out
    assert '"features"' in out


def test_an_unknown_subcommand_exits_with_an_error():
    with pytest.raises(SystemExit):
        main(["explain-my-recovery"])
