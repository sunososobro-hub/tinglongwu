"""#1920: CI cost per PR stays flat as pushes pile up. A new push to a PR cancels that PR's older, still-running
ci / mobile run; dev and main push runs and dispatches never share a group, so none is cancelled or replaced
(the release gate and the dev-tip `test` need a finished run on every push). Evaluates each workflow's
concurrency expressions per event."""
import re
from pathlib import Path

import pytest
import yaml

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows"
WORKFLOWS = ["ci.yml", "mobile-apk.yml", "mobile-ios.yml"]


def _ctx(event, pr=None, ref="refs/heads/dev", run_id=1):
    return {"github.event_name": event, "github.event.pull_request.number": pr, "github.ref": ref,
            "github.run_id": run_id}


def _eval(expr, ctx):
    """GitHub expression subset: names, 'literals', ==, !=, &&, ||, format(). Mapped onto Python's and / or,
    which short-circuit and return operands the same way GitHub's && / || do."""
    def one(m):
        src = m.group(1)
        out, pos = [], 0
        tok = re.compile(r"\s*(?:(?P<s>'[^']*')|(?P<op>==|!=|&&|\|\||[(),])|(?P<n>[A-Za-z_][\w.]*))")
        while pos < len(src.rstrip()):
            t = tok.match(src, pos)
            assert t, src[pos:]
            pos = t.end()
            if t.group("s"):
                out.append(repr(t.group("s")[1:-1]))
            elif t.group("op"):
                out.append({"&&": " and ", "||": " or "}.get(t.group("op"), t.group("op")))
            elif t.group("n") == "format":
                out.append("_fmt")
            else:
                out.append(f"_c[{t.group('n')!r}]")
        val = eval("".join(out), {"__builtins__": {}},  # noqa: S307 - built from the tokens above only
                   {"_c": ctx, "_fmt": lambda f, *a: f.format(*a)})
        return val

    if isinstance(expr, bool):
        return expr
    whole = re.fullmatch(r"\$\{\{(.*?)\}\}", expr.strip())
    if whole:
        return one(whole)
    return re.sub(r"\$\{\{(.*?)\}\}", lambda m: str(one(m)), expr)


def _conc(name, ctx):
    c = yaml.safe_load((WF / name).read_text(encoding="utf-8"))["concurrency"]
    return _eval(c["group"], ctx), _eval(c["cancel-in-progress"], ctx)


@pytest.mark.parametrize("name", WORKFLOWS)
def test_new_push_to_a_pr_cancels_its_older_run(name):
    g1, cancel = _conc(name, _ctx("pull_request", pr=1920, ref="refs/pull/1920/merge", run_id=10))
    g2, _ = _conc(name, _ctx("pull_request", pr=1920, ref="refs/pull/1920/merge", run_id=11))
    assert g1 == g2 and cancel is True


@pytest.mark.parametrize("name", WORKFLOWS)
def test_two_prs_never_share_a_group(name):
    assert _conc(name, _ctx("pull_request", pr=1))[0] != _conc(name, _ctx("pull_request", pr=11))[0]


@pytest.mark.parametrize("name", WORKFLOWS)
@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_push_and_dispatch_runs_are_never_cancelled_or_replaced(name, event):
    g1, cancel = _conc(name, _ctx(event, run_id=100))
    g2, _ = _conc(name, _ctx(event, run_id=101))
    assert cancel is False
    assert g1 != g2  # a pending dev push run would otherwise be replaced by the next one
    assert g1 != _conc(name, _ctx("pull_request", pr=100))[0]


def test_ci_group_names_differ_per_workflow():
    groups = {_conc(n, _ctx("pull_request", pr=7))[0] for n in WORKFLOWS}
    assert len(groups) == len(WORKFLOWS)
