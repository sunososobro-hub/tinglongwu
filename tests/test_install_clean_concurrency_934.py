"""#934: install-clean dispatches with tag=v0.3.4 and tag=v0.3.5 (before/after ttfs pair) were both cancelled
(runs 43662916343, 43662920828): every run on dev shared `install-clean-<ref>` with cancel-in-progress, so the
second dispatch and any dev push killed the first. Evaluates the workflow's concurrency expressions per event."""
import re
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "install-clean.yml"


def _ctx(event, tag="", ref="refs/heads/dev"):
    return {"github.ref": ref, "github.event_name": event, "github.event.inputs.tag": tag}


def _eval(expr, ctx):
    """Tiny evaluator for the expression forms this workflow uses: `${{ name }}` and `${{ name != 'lit' }}`."""
    def one(m):
        body = m.group(1).strip()
        cmp = re.fullmatch(r"([\w.]+)\s*(==|!=)\s*'([^']*)'", body)
        if cmp:
            eq = ctx[cmp.group(1)] == cmp.group(3)
            return str(eq if cmp.group(2) == "==" else not eq).lower()
        return ctx[body]
    if isinstance(expr, bool):
        return expr
    out = re.sub(r"\$\{\{(.*?)\}\}", one, expr)
    return {"true": True, "false": False}.get(out, out)


def _conc(ctx):
    c = yaml.safe_load(WF.read_text(encoding="utf-8"))["concurrency"]
    return _eval(c["group"], ctx), _eval(c["cancel-in-progress"], ctx)


def test_two_tag_dispatches_do_not_share_a_group():
    assert _conc(_ctx("workflow_dispatch", "v0.3.4"))[0] != _conc(_ctx("workflow_dispatch", "v0.3.5"))[0]


def test_dev_push_does_not_cancel_a_dispatch():
    assert _conc(_ctx("push"))[0] != _conc(_ctx("workflow_dispatch", "v0.3.4"))[0]
    assert _conc(_ctx("workflow_dispatch", "v0.3.4"))[1] is False


def test_pushes_still_cancel_older_pushes():
    g1, cancel = _conc(_ctx("push"))
    assert cancel is True and g1 == _conc(_ctx("push"))[0]


def test_snapshot_refs_stay_separate():
    assert _conc(_ctx("workflow_dispatch", ref="refs/heads/release/v0.3.7"))[0] != _conc(_ctx("workflow_dispatch"))[0]
