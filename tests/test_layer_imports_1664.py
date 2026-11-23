"""#1664 step 1: a lower layer must not import a higher one (system < core < flow < ui).

Layers follow the #1664 table. Every import counts, lazy ones inside functions too. ALLOWED lists the edges still
left on purpose; a new upward import fails this test (move the shared code down, or pass it in as a parameter)."""
from __future__ import annotations

import ast
from pathlib import Path

# #1664: the package is split over src/{system,core,flow,ui}/tinglongwu (core extends __path__)
PKGS = sorted((Path(__file__).resolve().parents[1] / "src").glob("*/tinglongwu"))
assert len(PKGS) == 4, PKGS


def _rel(mod_file: Path) -> Path:
    return next(mod_file.relative_to(p) for p in PKGS if mod_file.is_relative_to(p))

# face_to_face: listed as flow (#1664 left it open); after this step it imports only langs
LAYERS = {
    "ui": "gui overlay splash menuopts",
    "flow": "face_to_face cli pipeline stream voice record reverse selftest speaksmoke speedprobe doctor report minspec netreport "
            "mt_pending topics instances sink fmt backend_probe",
    "core": "asr asr_vulkan translate hymt fm ollama mt_backend engines llm_prompt lang langfollow hallucination "
            "repeat merge segment glossary adapt models speakers speaker_embed topic_segmenter report_translation data vulkan_probe",
    "system": "capture device hwprobe threads gpu_pack llamacheck userdirs diag offline vbcable pack_manifest "
              "speaker_pylib speaker_reqs i18n langs settings",
}
RANK = {"system": 0, "core": 1, "flow": 2, "ui": 3}
LAYER_OF = {m: layer for layer, mods in LAYERS.items() for m in mods.split()}

# importer -> imported top-level module: why the edge stays (each one is a later #1664 step)
ALLOWED = {
    ("cli", "gui"): "`tlwu --gui` entry dispatch; moves with the entry points",
    ("cli", "overlay"): "`tlwu --overlay` entry dispatch; moves with the entry points",
    ("cli", "splash"): "start-up splash played by the `tlwu` entry; moves with the entry points",
    ("reverse", "overlay"): "proto.main `--overlay` entry dispatch (hands OverlaySink to ListenSide)",
    ("speaksmoke", "gui"): "headless smoke of the GUI speak button (App.toggle); belongs with the ui layer",
}


def _top(mod_file: Path) -> str:
    rel = _rel(mod_file).with_suffix("")
    return rel.parts[0]


def _targets(mod_file: Path, node) -> list[str]:
    """Top-level tinglongwu modules an import node names."""
    if isinstance(node, ast.Import):
        return [a.name.split(".")[1] for a in node.names if a.name.startswith("tinglongwu.")]
    if node.level == 0:
        mod = node.module or ""
        if mod == "tinglongwu":
            return [a.name for a in node.names]
        return [mod.split(".")[1]] if mod.startswith("tinglongwu.") else []
    pkg = list(_rel(mod_file).parent.parts)
    up = pkg[: len(pkg) - (node.level - 1)] if node.level - 1 <= len(pkg) else None
    if up is None:
        return []
    path = up + (node.module.split(".") if node.module else [])
    return [path[0]] if path else [a.name for a in node.names]


def upward_imports() -> set[tuple[str, str]]:
    found = set()
    for f in (f for p in PKGS for f in p.rglob("*.py")):
        src = _top(f)
        if src not in LAYER_OF:
            continue
        for node in ast.walk(ast.parse(f.read_text("utf-8"))):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for dst in _targets(f, node):
                    if dst in LAYER_OF and RANK[LAYER_OF[dst]] > RANK[LAYER_OF[src]]:
                        found.add((src, dst))
    return found


def test_no_new_upward_imports():
    extra = upward_imports() - set(ALLOWED)
    assert not extra, f"lower layer imports a higher one (#1664): {sorted(extra)}"


def test_allowlist_has_no_stale_entries():
    stale = set(ALLOWED) - upward_imports()
    assert not stale, f"fixed edges still in ALLOWED, remove them: {sorted(stale)}"


def test_moved_names_still_import_from_old_paths():
    from tinglongwu import engines, langs, settings, vbcable, device
    from tinglongwu.gui import core
    from tinglongwu.reverse import vmic

    assert core.load_settings is settings.load_settings and core.DEFAULTS is settings.DEFAULTS
    assert core.settings_path is settings.settings_path and core.save_settings is settings.save_settings
    assert core.lang_base is langs.lang_base and core.SameLanguageError is langs.SameLanguageError
    assert engines.free_vram_mib is device.free_vram_mib
    assert vmic.pick_win_cable is vbcable.pick_win_cable and vmic.WIN_CABLE == vbcable.WIN_CABLE


def test_no_tinglongwu_dir_at_repo_root():
    # a root tinglongwu/ (no __init__.py) is picked up as a namespace package from the cwd and shadows the
    # src/*/tinglongwu split: "cannot import name 'cli' from 'tinglongwu' (unknown location)". New files go under src/.
    assert not (Path(__file__).resolve().parents[1] / "tinglongwu").exists()
