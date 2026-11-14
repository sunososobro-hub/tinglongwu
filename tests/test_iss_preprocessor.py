"""ISPP treats any line starting with '#' as a directive (#901): a Pascal line that
starts with a '#13#10' char literal breaks ISCC."""
from pathlib import Path

DIRECTIVES = {"define", "undef", "include", "if", "ifdef", "ifndef", "else", "elif",
              "endif", "emit", "pragma", "error", "preproc", "dim", "redim", "sub",
              "endsub", "for", "insert", "append", "expr", "ifexist", "ifnexist"}


def bad_directive_lines(text):
    bad = []
    for n, line in enumerate(text.splitlines(), 1):
        s = line.lstrip()
        if s.startswith("#"):
            word = s[1:].split(None, 1)[0] if s[1:].strip() else ""
            word = "".join(c for c in word if c.isalpha()).lower()
            if word not in DIRECTIVES:
                bad.append(n)
    return bad


def test_detector_flags_char_literal_line():
    assert bad_directive_lines("x :=\n      #13#10 + 'a';\n") == [2]
    assert bad_directive_lines("#ifdef CoreLayout\n#else\n#endif\n") == []


def test_installer_script_has_no_bad_directive_lines():
    iss = Path(__file__).resolve().parent.parent / "installer" / "tinglongwu.iss"
    assert bad_directive_lines(iss.read_text(encoding="utf-8")) == []
