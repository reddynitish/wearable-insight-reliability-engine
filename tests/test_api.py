"""The HTTP surface. It must translate, never decide."""
from __future__ import annotations

import pytest
from conftest import rhr_request
from fastapi.testclient import TestClient

from engine import MODEL_VERSION, POLICY_VERSION
from engine.api.app import app
from engine.engine import evaluate
from engine.policies import supported_claim_types


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_health(client):
    body = client.get("/v1/health").json()
    assert body["status"] == "ok"
    assert body["model_version"] == MODEL_VERSION
    assert body["policy_version"] == POLICY_VERSION
    assert body["claim_types"] == supported_claim_types()
    assert "not a medical assessment" in body["scope"]


def test_decision_endpoint_matches_the_library(client):
    """The API must add nothing: same request, byte-identical decision."""
    request = rhr_request()
    response = client.post("/v1/decisions", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json() == evaluate(request).model_dump(mode="json")


def test_decision_endpoint_returns_a_typed_reject_for_an_unknown_claim_type(client):
    """Not a 4xx: the caller still needs a decision it can branch on."""
    request = rhr_request(claim_type="YOU_ARE_BURNT_OUT")
    response = client.post("/v1/decisions", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "REJECT"
    assert body["reason_codes"] == ["UNSUPPORTED_CLAIM_TYPE"]


def test_malformed_request_is_a_422(client):
    """A naive timestamp is a caller bug, not an evidence state."""
    payload = rhr_request().model_dump(mode="json")
    payload["observations"][0]["measured_at"] = "2026-09-23T11:00:00"
    assert client.post("/v1/decisions", json=payload).status_code == 422


def test_personal_subject_id_is_rejected_at_the_boundary(client):
    payload = rhr_request().model_dump(mode="json")
    payload["subject_id"] = "someone@example.com"
    response = client.post("/v1/decisions", json=payload)
    assert response.status_code == 422
    assert "pseudonymous" in response.text


def test_claim_types_endpoint_exposes_the_contract(client):
    body = client.get("/v1/claim-types").json()
    assert len(body) == len(supported_claim_types())
    rhr = next(c for c in body if c["claim_type"] == "RESTING_HEART_RATE_ELEVATED")
    assert rhr["required_signals"] == ["resting_heart_rate"]
    assert rhr["requirements"]["min_baseline_days"] == 28
    assert rhr["requirements"]["show_z"] == 1.5
    assert rhr["known_confounders"]


def test_reason_codes_endpoint_lists_every_code_with_its_effect(client):
    from engine.reasons import ReasonCode

    body = client.get("/v1/reason-codes").json()
    assert {row["code"] for row in body} == {c.value for c in ReasonCode}
    for row in body:
        assert row["forces"] in {"reject", "wait", "warn", "note"}


def test_corruptions_endpoint(client):
    body = client.get("/v1/corruptions").json()
    assert body["severities"] == ["mild", "moderate", "severe"]
    assert len(body["corruptions"]) >= 17


def test_demo_case_endpoint_shows_the_decision_changing(client):
    clean = client.get("/v1/demo/case?claim_type=RESTING_HEART_RATE_ELEVATED&seed=0").json()
    assert clean["synthetic"] is True
    assert clean["decision"]["decision"] in ("SHOW", "SHOW_WITH_WARNING")

    broken = client.get(
        "/v1/demo/case?claim_type=RESTING_HEART_RATE_ELEVATED&seed=0"
        "&corruption=truncate_baseline&severity=severe"
    ).json()
    assert broken["decision"]["decision"] == "WAIT_FOR_MORE_DATA"
    assert "BASELINE_NOT_MATURE" in broken["decision"]["reason_codes"]


def test_demo_case_rejects_an_unknown_claim_type_or_corruption(client):
    assert "error" in client.get("/v1/demo/case?claim_type=NOPE").json()
    assert "error" in client.get("/v1/demo/case?corruption=nope").json()


def test_demo_page_renders_and_names_its_limits(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Not a medical device" in response.text
    assert "synthetic" in response.text


def test_openapi_schema_is_generated(client):
    schema = client.get("/openapi.json").json()
    assert "/v1/decisions" in schema["paths"]
    assert schema["info"]["version"] == MODEL_VERSION
