"""#615: optional packs (speak, meeting-notes, languages): manifest, upgrade plan, removed-pack message, release notes."""
import importlib.util
import json
from pathlib import Path

import pytest

from tinglongwu import pack_manifest as pm

ROOT = Path(__file__).resolve().parent.parent
H = "ab" * 32


def entries():
    return {n: {"size": s, "sha256": H, "url": f"https://x/{n}.zip"}
            for n, s in {"speak": 300_000_000, "meeting-notes": 800_000_000, "ja": 1_100_000_000, "ko": 1_100_000_000}.items()}


def test_manifest_lists_every_pack_with_size_and_sha(tmp_path):
    man = pm.make_manifest(entries())
    assert set(man["packs"]) == {"speak", "meeting-notes", "ja", "ko"}
    assert man["packs"]["speak"]["feature"] == "speak" and man["packs"]["ja"]["feature"] == "language"
    f = tmp_path / "m.json"
    f.write_text(json.dumps(man))
    assert pm.load_manifest(f) == man


def test_bad_manifest_rejected(tmp_path):
    bad = pm.make_manifest(entries())
    bad["packs"]["ja"]["sha256"] = "short"
    with pytest.raises(pm.ManifestError):
        pm.validate(bad)
    (tmp_path / "m.json").write_text("{not json")
    with pytest.raises(pm.ManifestError):
        pm.load_manifest(tmp_path / "m.json")


def test_upgrade_downloads_none_of_installed_packs(tmp_path):
    man = pm.make_manifest(entries())
    state = tmp_path / "installed.json"
    state.write_text(json.dumps({"speak": H, "ja": "00" * 32}))  # ja: older build on disk
    plan = pm.plan_upgrade(man, pm.load_installed(state), wanted=["speak", "ja", "ko"])
    assert plan["download"] == ["ko"]
    assert plan["keep"] == ["ja", "speak"] and plan["outdated"] == ["ja"]
    assert pm.plan_upgrade(man, pm.load_installed(state), wanted=["speak", "ja"])["download"] == []
    assert pm.load_installed(tmp_path / "missing.json") == {}


def test_removed_pack_disables_only_its_feature():
    man = pm.make_manifest(entries())
    ok, msg = pm.feature_status("speak", man, {"speak": H, "meeting-notes": H})
    assert ok and msg == ""
    ok, msg = pm.feature_status("meeting-notes", man, {"speak": H})
    assert not ok and "meeting notes is off" in msg and "meeting-notes" in msg
    assert pm.feature_status("speak", man, {"speak": H})[0]  # speak unaffected


def test_release_notes_list_every_pack_size():
    spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts/release_notes.py")
    rn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rn)
    man = pm.make_manifest(entries())
    text = rn.release_notes("## What's new\n- TODO: x\n", "", None, "", "", "", [], {}, man)
    for name, mb in (("speak", "300.0"), ("meeting-notes", "800.0"), ("ja", "1100.0"), ("ko", "1100.0")):
        assert f"| {name} | {mb} MB |" in text
    assert "Optional packs" not in rn.release_notes("## What's new\n", "", None, "", "", "", [], {})
