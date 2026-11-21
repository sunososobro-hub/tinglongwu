"""#936: stats-dashboard must not depend on a single hourly cron."""
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "stats-dashboard.yml"


def _on():
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return doc.get("on", doc.get(True)), doc


def test_two_cron_offsets():
    on, _ = _on()
    crons = {c["cron"] for c in on["schedule"]}
    assert {"17 * * * *", "47 * * * *"} <= crons


def test_runs_after_release_only_on_main_1144():
    on, _ = _on()
    wr = on["workflow_run"]
    assert set(wr["workflows"]) == {"release"}
    assert wr["types"] == ["completed"]
    assert wr["branches"] == ["main"]


def test_runs_on_dev_push_1713():
    on, _ = _on()
    assert on["push"]["branches"] == ["dev"]
    assert "workflow_dispatch" in on
    assert doc_perms() == {"contents": "write"}


def doc_perms():
    return _on()[1]["permissions"]


def test_concurrency_collapses_runs():
    _, doc = _on()
    assert doc["concurrency"]["group"] == "stats-dashboard"
    assert doc["concurrency"]["cancel-in-progress"] is True
