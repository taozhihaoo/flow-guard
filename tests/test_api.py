"""Tests for the FastAPI REST service (Phase 10)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flowguard.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(db_path=":memory:"))


@pytest.fixture()
def ok_config(tmp_path: Path) -> Path:
    path = tmp_path / "wf.yml"
    path.write_text(
        "workflow:\n"
        "  name: api-demo\n"
        "  steps:\n"
        "    - name: hello\n"
        f"      command: [{sys.executable}, -c, \"print('from api')\"]\n",
        encoding="utf-8",
    )
    return path


def inline_config(name: str = "inline-wf", *, failing: bool = False) -> dict:
    code = "raise SystemExit(1)" if failing else "print('inline ok')"
    return {
        "workflow": {
            "name": name,
            "steps": [{"name": "step-1", "command": [sys.executable, "-c", code]}],
        }
    }


class TestHealth:
    def test_health_ok(self, client: TestClient):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_health_reports_version(self, client: TestClient):
        from flowguard import __version__

        assert client.get("/health").json()["version"] == __version__


class TestExecute:
    def test_execute_inline_config(self, client: TestClient):
        response = client.post("/execute", json={"config": inline_config()})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "succeeded"
        assert body["run"]["workflow_name"] == "inline-wf"
        assert body["steps"][0]["stdout"].strip() == "inline ok"
        assert body["steps"][0]["exit_code"] == 0

    def test_execute_by_path(self, client: TestClient, ok_config: Path):
        response = client.post("/execute", json={"path": str(ok_config)})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "succeeded"
        assert body["run"]["workflow_name"] == "api-demo"

    def test_execute_failure_still_recorded(self, client: TestClient):
        response = client.post("/execute", json={"config": inline_config("failing", failing=True)})
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "failed"
        assert body["run"]["status"] == "failed"
        assert body["steps"][0]["exit_code"] == 1

    def test_execute_rejects_path_and_config_together(self, client: TestClient):
        response = client.post(
            "/execute",
            json={"path": "x.yml", "config": inline_config()},
        )
        assert response.status_code == 422
        assert "exactly one" in response.json()["detail"]

    def test_execute_rejects_missing_both(self, client: TestClient):
        response = client.post("/execute", json={"env": {"A": "1"}})
        assert response.status_code == 422

    def test_execute_invalid_config_returns_400(self, client: TestClient):
        response = client.post(
            "/execute",
            json={"config": {"workflow": {"name": "bad", "steps": []}}},
        )
        assert response.status_code == 400
        assert "steps" in response.json()["detail"]

    def test_execute_missing_file_returns_400(self, client: TestClient):
        response = client.post("/execute", json={"path": "no/such/file.yml"})
        assert response.status_code == 400
        assert "not found" in response.json()["detail"]

    def test_execute_env_overrides(self, client: TestClient):
        config = {
            "workflow": {
                "name": "env-api",
                "steps": [
                    {
                        "name": "check",
                        "command": [
                            sys.executable,
                            "-c",
                            "import os; raise SystemExit(0 if os.environ['API_VAR']=='set' else 1)",
                        ],
                    }
                ],
            }
        }
        response = client.post("/execute", json={"config": config, "env": {"API_VAR": "set"}})
        assert response.json()["status"] == "succeeded"

    def test_execute_is_recorded(self, client: TestClient):
        client.post("/execute", json={"config": inline_config()})
        runs = client.get("/runs").json()["runs"]
        assert len(runs) == 1
        assert runs[0]["workflow_name"] == "inline-wf"


class TestRuns:
    def test_list_runs_empty(self, client: TestClient):
        response = client.get("/runs")
        assert response.status_code == 200
        assert response.json() == {"count": 0, "runs": []}

    def test_get_run_by_id(self, client: TestClient):
        run_id = client.post("/execute", json={"config": inline_config()}).json()["run"]["run_id"]
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["run"]["run_id"] == run_id
        assert len(body["steps"]) == 1
        assert isinstance(body["events"], list)

    def test_get_unknown_run_404(self, client: TestClient):
        response = client.get("/runs/does-not-exist")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_list_runs_limit(self, client: TestClient):
        for index in range(3):
            client.post("/execute", json={"config": inline_config(f"wf-{index}")})
        body = client.get("/runs", params={"limit": 2}).json()
        assert body["count"] == 2
        assert len(body["runs"]) == 2

    def test_list_runs_most_recent_first(self, client: TestClient):
        client.post("/execute", json={"config": inline_config("first")})
        client.post("/execute", json={"config": inline_config("second")})
        runs = client.get("/runs").json()["runs"]
        assert runs[0]["workflow_name"] == "second"

    def test_list_runs_workflow_filter(self, client: TestClient):
        client.post("/execute", json={"config": inline_config("alpha")})
        client.post("/execute", json={"config": inline_config("beta")})
        runs = client.get("/runs", params={"workflow": "alpha"}).json()["runs"]
        assert len(runs) == 1
        assert runs[0]["workflow_name"] == "alpha"

    def test_list_runs_status_filter(self, client: TestClient):
        client.post("/execute", json={"config": inline_config("good")})
        client.post("/execute", json={"config": inline_config("bad", failing=True)})
        failed = client.get("/runs", params={"status": "failed"}).json()["runs"]
        succeeded = client.get("/runs", params={"status": "succeeded"}).json()["runs"]
        assert len(failed) == 1
        assert len(succeeded) == 1

    def test_run_steps_include_logs(self, client: TestClient):
        client.post("/execute", json={"config": inline_config()})
        body = client.get("/runs").json()
        detail = client.get(f"/runs/{body['runs'][0]['run_id']}").json()
        assert "inline ok" in detail["steps"][0]["stdout"]
        assert detail["steps"][0]["duration_ms"] >= 0


class TestOpenApi:
    def test_openapi_schema_lists_all_endpoints(self, client: TestClient):
        schema = client.get("/openapi.json").json()
        paths = set(schema["paths"])
        assert paths == {"/health", "/runs", "/runs/{run_id}", "/execute"}
