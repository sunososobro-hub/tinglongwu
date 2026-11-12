import importlib.util
import pathlib

spec = importlib.util.spec_from_file_location(
    "external_scan", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "external_scan.py")
es = importlib.util.module_from_spec(spec)
spec.loader.exec_module(es)

R = "o/r"
U = lambda n: {"login": n}


def fake(path):
    table = {
        f"repos/{R}/issues?state=open&per_page=100": [
            {"number": 1, "title": "outsider bug", "user": U("bob")},
            {"number": 2, "title": "mine", "user": U("o")},
            {"number": 3, "title": "pr as issue", "user": U("carl"), "pull_request": {}},
        ],
        f"repos/{R}/pulls?state=open&per_page=100": [{"number": 3, "title": "ext pr", "user": U("carl")}],
        f"repos/{R}/issues/1/comments?per_page=100": [{"user": U("bob")}, {"user": U("o")}],
        f"repos/{R}/issues/2/comments?per_page=100": [{"user": U("o")}, {"user": U("dan")}],
        f"repos/{R}/issues/3/comments?per_page=100": [],
        f"repos/{R}": {"default_branch": "dev"},
        f"repos/{R}/forks?per_page=100": [
            {"full_name": "a/r", "owner": U("a"), "default_branch": "dev"},
            {"full_name": "b/r", "owner": U("b"), "default_branch": "dev"},
        ],
        f"repos/{R}/compare/dev...a:dev?per_page=1": {"ahead_by": 4},
        f"repos/{R}/compare/dev...b:dev?per_page=1": {"ahead_by": 0},
    }
    return table[path]


def test_four_sections():
    out = es.render(es.scan(fake, repo=R, owner="o"))
    assert out.count("==") == 8
    assert "#1 outsider bug (@bob)" in out and "#2 mine" not in out
    assert "#3 ext pr (@carl)" in out
    assert "#2 by @dan" in out and "#1 by @bob" not in out
    assert "a/r ahead by 4" in out and "b/r" not in out
