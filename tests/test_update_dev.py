"""#84: update.bat -> tinglongwu_setup.update. Runs the real git steps against a temp origin + clone."""
import subprocess

import pytest

from tinglongwu_setup import update


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=str(cwd), check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, name, text):
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", name)


@pytest.fixture
def repos(tmp_path):
    """origin (bare) with a dev branch, a dev checkout of it, and an upstream clone that adds a new commit."""
    origin, up, me = tmp_path / "origin.git", tmp_path / "up", tmp_path / "me"
    git(tmp_path, "init", "-q", "--bare", "-b", "dev", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(up))
    git(up, "checkout", "-q", "-b", "dev")
    commit(up, "a.txt", "1")
    (up / "requirements.txt").write_text("")
    commit(up, "requirements.txt", "")
    git(up, "push", "-q", "origin", "dev")
    git(tmp_path, "clone", "-q", "-b", "dev", str(origin), str(me))
    commit(up, "b.txt", "new")
    git(up, "push", "-q", "origin", "dev")
    exe = tmp_path / "tlwu-gui.exe"
    exe.write_text("")
    return origin, up, me, exe


class Rec:
    def __init__(self, pip_rc=0):
        self.pip, self.launched, self.lines, self.pip_rc = [], [], [], pip_rc

    def pip_fn(self, args, cwd):
        self.pip.append(args)
        return self.pip_rc

    def launch(self, exe, cwd):
        self.launched.append(exe)

    def run(self, me, exe):
        return update.main(me, pip=self.pip_fn, launch=self.launch, exe=exe, say=self.lines.append)


def test_behind_origin_pulls_installs_and_starts_gui(repos):
    origin, up, me, exe = repos
    r = Rec()
    assert r.run(me, exe) == 0
    assert (me / "b.txt").read_text() == "new"
    assert git(me, "rev-parse", "HEAD") == git(up, "rev-parse", "HEAD")
    assert r.pip == [["install", "-r", "requirements.txt"], ["install", "-e", ".", "--no-deps"]]
    assert r.launched == [exe]
    assert any(line.startswith("  updated ") for line in r.lines)


def test_up_to_date_still_installs_and_starts(repos):
    origin, up, me, exe = repos
    Rec().run(me, exe)
    r = Rec()
    assert r.run(me, exe) == 0
    assert any("already up to date" in line for line in r.lines)
    assert r.launched == [exe]


def test_local_changes_stop_before_pull(repos):
    origin, up, me, exe = repos
    (me / "a.txt").write_text("edited")
    r = Rec()
    assert r.run(me, exe) == 1
    assert not (me / "b.txt").exists() and r.pip == [] and r.launched == []
    assert any("a.txt" in line for line in r.lines)


def test_other_branch_stops(repos):
    origin, up, me, exe = repos
    git(me, "checkout", "-q", "-b", "feature")
    r = Rec()
    assert r.run(me, exe) == 1
    assert r.pip == [] and any("'feature'" in line for line in r.lines)


def test_diverged_dev_stops(repos):
    origin, up, me, exe = repos
    commit(me, "mine.txt", "local")
    r = Rec()
    assert r.run(me, exe) == 1
    assert not (me / "b.txt").exists() and r.pip == []
    assert any("no fast-forward" in line for line in r.lines)


def test_not_a_git_checkout(tmp_path):
    exe = tmp_path / "tlwu-gui.exe"
    exe.write_text("")
    r = Rec()
    assert r.run(tmp_path, exe) == 1
    assert any("not a git checkout" in line for line in r.lines)


def test_pip_failure_does_not_start_gui(repos):
    origin, up, me, exe = repos
    r = Rec(pip_rc=1)
    assert r.run(me, exe) == 1
    assert r.launched == [] and len(r.pip) == 1


def test_missing_gui_exe(repos, tmp_path):
    origin, up, me, exe = repos
    r = Rec()
    assert r.run(me, tmp_path / "nope.exe") == 1
    assert r.launched == [] and any("install.bat" in line for line in r.lines)


def test_gui_exe_sits_next_to_python(tmp_path):
    p = update.gui_exe(tmp_path / "Scripts" / "python.exe")
    assert p.parent == tmp_path / "Scripts" and p.name.startswith("tlwu-gui")
