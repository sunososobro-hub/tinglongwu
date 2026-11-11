"""#42: the menus' listen side passes --glossary (user glossary folder) and, on CPU with Hy-MT2 installed, offers
Hy-MT2 instead of the OPUS ru->en->zh pivot; plus a negation regression for the OPUS glossary `~` fix."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tinglongwu import glossary, menuopts
from tinglongwu.translate import PivotTranslator

ROOT = Path(__file__).resolve().parent.parent
HYMT_REL = Path("hymt2-1.8b") / "Hy-MT2-1.8B-Q4_K_M.gguf"


@pytest.fixture(autouse=True)
def _cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("TLWU_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("TLWU_UI_LANG", "en")


def _args(answer="", device="cpu", cached=None, ready=True, asked=None):
    def ask(prompt):
        if asked is not None:
            asked.append(prompt)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    return menuopts.listen_args(ask=ask, say=lambda m: None, device=lambda: device, mdir=Path("."),
                                cpu_choice=lambda: cached, hymt_ready=lambda d: ready)


def test_cpu_with_hymt_defaults_to_hymt_without_asking():
    asked = []
    assert _args("", asked=asked) == ["--engine", "hymt"] and asked == []


@pytest.mark.parametrize("kw", [dict(device="cuda"), dict(cached=object()), dict(ready=False)])
def test_no_question_when_auto_already_decides(kw):
    asked = []
    assert _args("h", asked=asked, **kw) == [] and asked == []


def test_glossary_folder_with_txt_is_passed(tmp_path):
    d = tmp_path / "cfg" / "glossary"
    d.mkdir(parents=True)
    assert _args(device="cuda") == []          # empty folder: nothing
    (d / "wifi.txt").write_text("роутер = 路由器\n", "utf-8")
    assert _args(device="cuda") == ["--glossary", str(d)]
    assert _args("h") == ["--glossary", str(d), "--engine", "hymt"]


def test_broken_device_check_never_stops_the_menu():
    def boom():
        raise RuntimeError("probe exploded")

    assert menuopts.listen_args(ask=lambda p: "h", say=lambda m: None, device=boom, mdir=Path("."),
                                cpu_choice=lambda: None, hymt_ready=lambda d: True) == []


def _fake_env(tmp_path):
    """A repo copy whose .venv puts a recording `tlwu` stub on PATH; models dir with OPUS pairs + Hy-MT2 GGUF;
    a fake llama_cpp module so Hy-MT2 counts as installed. No GPU in the test environment -> CPU."""
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "scripts", root / "scripts")
    stub = tmp_path / "bin"
    stub.mkdir()
    log = tmp_path / "tlwu_args"
    (stub / "tlwu").write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "{log}"\n', "utf-8")
    (stub / "tlwu").chmod(0o755)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "activate").write_text(f'export PATH="{stub}:$PATH"\n', "utf-8")
    mdir = tmp_path / "models"
    for p in ("en-zh", "ru-en"):
        (mdir / "opus" / p).mkdir(parents=True)
    (mdir / HYMT_REL).parent.mkdir(parents=True)
    (mdir / HYMT_REL).write_bytes(b"gguf")
    fake = tmp_path / "pyfake"
    (fake / "llama_cpp").mkdir(parents=True)
    (fake / "llama_cpp" / "__init__.py").write_text("", "utf-8")
    gl = tmp_path / "cfg" / "glossary"
    gl.mkdir(parents=True)
    (gl / "topic.txt").write_text("так себе = 一般般 ~ 還不錯 | 很好\n", "utf-8")
    env = dict(os.environ, HOME=str(tmp_path / "home"), TLWU_MODELS=str(mdir), TLWU_NO_UPDATE="1",
               TLWU_CONFIG_DIR=str(tmp_path / "cfg"), TLWU_UI_LANG="en", TLWU_LANG="auto",
               PYTHONPATH=os.pathsep.join([str(fake), str(ROOT)]))
    env.pop("APPDATA", None)
    env.pop("WHISPER", None)
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    return root, log, env, gl


@pytest.mark.skipif(sys.platform == "win32", reason="bash menu")
@pytest.mark.parametrize("answer,engine", [("", ["--engine", "hymt"])])
def test_linux_menu_listen_passes_glossary_and_cpu_engine(tmp_path, answer, engine):
    root, log, env, gl = _fake_env(tmp_path)
    # 3 = listen only; Enter = do not save subtitles; then the live-drafts question (CPU: Enter = on)
    r = subprocess.run(["bash", str(root / "scripts" / "linux_menu.sh")], input=f"3\n\n{answer}\n", env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    args = log.read_text("utf-8").split("\n")[:-1]
    assert args[:4] == ["--lang", "auto", "--lang-candidates", "en,ru,ja,zh"]
    assert args[4:] == ["--glossary", str(gl)] + engine + ["--stream"]   # #257: CPU listen-only, Enter = drafts on
    assert "Glossary: 1 file(s)" in r.stderr


def test_negation_glossary_fix_through_opus_pivot():
    """The reported case: a colloquial negative ('so-so') came out positive through ru->en->zh."""
    table = {"Фильм так себе.": "The movie is good.", "The movie is good.": "電影還不錯。"}

    def factory(s, t):
        return lambda text: table[text]

    tr = PivotTranslator(None, "tw", step_factory=factory)
    tr.glossary = glossary.Glossary(glossary.parse("так себе = 一般般 ~ 還不錯 | 很好"))
    assert tr.translate("Фильм так себе.", "ru", "zh").text == "電影一般般。"
    # a line without the entry is left alone
    table.update({"Фильм хороший.": "The movie is good."})
    assert tr.translate("Фильм хороший.", "ru", "zh").text == "電影還不錯。"


@pytest.mark.parametrize("device,both,expected", [("cpu", False, True), ("cpu", True, False),
                                                  ("cuda", False, True), ("cuda", True, False)])
def test_stream_default_menuopts(device, both, expected):  # #257, #273: listen-only drafts on for GPU too
    assert menuopts.stream_default(device, both) is expected


def test_stream_default_cli_prints_flag():
    r = subprocess.run([sys.executable, "-m", "tinglongwu.menuopts", "stream-default", "both"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and r.stdout.strip() == "0"


def test_linux_menu_drafts_n_turns_stream_off(tmp_path):  # #257 acceptance 3
    root, log, env, gl = _fake_env(tmp_path)
    r = subprocess.run(["bash", str(root / "scripts" / "linux_menu.sh")], input="3\n\nn\n", env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "--stream" not in log.read_text("utf-8").split("\n")
