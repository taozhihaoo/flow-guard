"""Tests for the built-in, bilingual web dashboard."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from flowguard.api.app import create_app
from flowguard.api.dashboard import render_dashboard


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(db_path=":memory:"))


class TestDashboardPage:
    def test_root_serves_html_in_english_by_default(self, client: TestClient, monkeypatch):
        monkeypatch.delenv("FLOWGUARD_LANG", raising=False)
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Your pipelines, at a glance." in response.text
        assert "__FLOWGUARD_I18N__" not in response.text

    def test_query_parameter_switches_to_chinese(self, client: TestClient):
        response = client.get("/", params={"lang": "zh-CN"})
        assert response.status_code == 200
        assert "流水线状态" in response.text
        assert '<html lang="zh">' in response.text

    def test_env_variable_sets_default_language(self, client: TestClient, monkeypatch):
        monkeypatch.setenv("FLOWGUARD_LANG", "zh")
        assert "流水线状态" in client.get("/").text

    def test_dashboard_is_excluded_from_openapi(self, client: TestClient):
        paths = set(client.get("/openapi.json").json()["paths"])
        assert "/" not in paths
        assert {"/health", "/runs", "/runs/{run_id}", "/execute"} <= paths

    def test_no_external_assets(self, client: TestClient):
        html = client.get("/").text
        assert "http://" not in html
        assert "https://" not in html
        assert "<script src=" not in html
        assert "<link " not in html

    def test_no_unresolved_template_placeholders(self, client: TestClient):
        html = client.get("/").text
        for placeholder in ("__TITLE__", "__LANG__", "__FLOWGUARD_I18N__"):
            assert placeholder not in html


class TestDashboardRendering:
    def test_json_island_is_valid_and_carries_metadata(self):
        html = render_dashboard("en")
        start = html.index('<script id="flowguard-i18n" type="application/json">')
        start = html.index(">", start) + 1
        end = html.index("</script>", start)
        payload = json.loads(html[start:end])
        assert payload["__lang__"] == "en"
        assert payload["__version__"]
        assert payload["dash.title"] == "FlowGuard · Dashboard"

    def test_server_data_is_never_injected_into_html(self):
        # Run names reach the page only at runtime via fetch()/textContent.
        html = render_dashboard("en")
        assert "textContent" in html
        assert "innerHTML = run" not in html
