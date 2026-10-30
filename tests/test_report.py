import json

from amagi_staleref import report
from amagi_staleref.checks import Finding
from amagi_staleref.rules import RULES


def result(findings=(), files=None, checked=0, roots_without_files=()):
    findings = list(findings)
    if files is None:
        files = sorted({f.file for f in findings})
    return {"files": list(files), "findings": findings, "checked": checked,
            "roots_without_files": list(roots_without_files)}


ERR = Finding("CLAUDE.md", 3, "SR001", "src/gone.py does not exist", "src/gone.py")
WARN = Finding("CLAUDE.md", 12, "SR040", "MY_VAR is not used anywhere else", "MY_VAR")
README_ERR = Finding("README.md", 7, "SR010", "npm run lint: no such script", "lint")


# ---------------------------------------------------------------- text
def test_text_nothing_stale():
    out = report.text(result(files=["CLAUDE.md"], checked=4))
    assert out == "Checked 4 references in 1 file. Nothing stale."


def test_text_singular_and_plural():
    out = report.text(result(files=["CLAUDE.md"], checked=1))
    assert out.startswith("Checked 1 reference in 1 file.")
    out = report.text(result(files=["A.md", "B.md"], checked=0))
    assert out.startswith("Checked 0 references in 2 files.")


def test_text_groups_by_file_in_files_order():
    res = result([README_ERR, ERR, WARN], files=["CLAUDE.md", "README.md"], checked=9)
    lines = report.text(res).splitlines()
    assert lines[0] == "CLAUDE.md"
    assert lines[1] == "     3  error    SR001  src/gone.py does not exist"
    assert lines[2] == "    12  warning  SR040  MY_VAR is not used anywhere else"
    assert lines[3] == ""
    assert lines[4] == "README.md"
    assert lines[5] == "     7  error    SR010  npm run lint: no such script"
    assert lines[6] == ""
    assert lines[7] == "Checked 9 references in 2 files. 3 stale: 2 errors, 1 warning."


def test_text_skips_files_without_findings():
    res = result([WARN], files=["AGENTS.md", "CLAUDE.md"], checked=2)
    out = report.text(res)
    assert "AGENTS.md" not in out
    assert out.splitlines()[0] == "CLAUDE.md"


def test_text_summary_omits_zero_severities():
    err = Finding("CLAUDE.md", 1, "SR001", "x", "x")
    errors_only = result([err, err], checked=2)
    assert report.text(errors_only).endswith("2 stale: 2 errors.")
    assert report.text(result([WARN], checked=1)).endswith("1 stale: 1 warning.")


def test_text_info_severity_is_counted(monkeypatch):
    monkeypatch.setitem(RULES, "SR999", ("test-info", "info", "test rule"))
    f = Finding("CLAUDE.md", 2, "SR999", "fyi", "x")
    out = report.text(result([f], checked=1))
    assert "     2  info     SR999  fyi" in out
    assert out.endswith("1 stale: 1 info.")


def test_text_wide_line_numbers_are_not_truncated():
    f = Finding("CLAUDE.md", 12345, "SR001", "m", "r")
    assert "  12345  error    SR001  m" in report.text(result([f]))


def test_text_roots_without_files_note():
    res = result(files=[], roots_without_files=["/tmp/empty"])
    out = report.text(res)
    assert out.splitlines() == [
        "Checked 0 references in 0 files. Nothing stale.",
        "(no CLAUDE.md, AGENTS.md, .cursorrules or README found under /tmp/empty)",
    ]


def test_text_quiet_hides_roots_without_files():
    res = result(files=[], roots_without_files=["/tmp/empty"])
    assert report.text(res, quiet=True) == "Checked 0 references in 0 files. Nothing stale."


def test_text_tolerates_missing_roots_key():
    res = {"files": ["CLAUDE.md"], "findings": [], "checked": 0}
    assert report.text(res) == "Checked 0 references in 1 file. Nothing stale."


# ---------------------------------------------------------------- json
def test_as_json_shape():
    res = result([ERR, WARN], checked=5)
    data = json.loads(report.as_json(res))
    assert data["files"] == ["CLAUDE.md"]
    assert data["checked"] == 5
    assert data["summary"] == {"error": 1, "warning": 1, "info": 0}
    assert data["findings"][0] == {
        "file": "CLAUDE.md", "line": 3, "rule": "SR001", "slug": "path-missing",
        "severity": "error", "ref": "src/gone.py", "message": "src/gone.py does not exist",
    }
    assert data["findings"][1]["slug"] == "env-var-unused"
    assert data["findings"][1]["severity"] == "warning"


def test_as_json_empty():
    data = json.loads(report.as_json(result(files=[], checked=0)))
    assert data == {"files": [], "checked": 0,
                    "summary": {"error": 0, "warning": 0, "info": 0}, "findings": []}


def test_as_json_is_indented_and_keeps_order():
    out = report.as_json(result([WARN, ERR]))
    assert out.startswith('{\n  "files"')
    assert [f["line"] for f in json.loads(out)["findings"]] == [12, 3]


def test_as_json_round_trips_unicode_and_quotes():
    f = Finding("docs/ünï.md", 1, "SR005", 'link "café.md" is missing', "café.md")
    data = json.loads(report.as_json(result([f])))
    assert data["findings"][0]["file"] == "docs/ünï.md"
    assert data["findings"][0]["message"] == 'link "café.md" is missing'


# ---------------------------------------------------------------- github
def test_github_annotation_levels(monkeypatch):
    monkeypatch.setitem(RULES, "SR999", ("test-info", "info", "test rule"))
    info = Finding("AGENTS.md", 4, "SR999", "fyi", "x")
    lines = report.github(result([ERR, WARN, info], checked=3)).splitlines()
    assert lines[0] == ("::error file=CLAUDE.md,line=3,title=SR001 path-missing"
                        "::src/gone.py does not exist")
    assert lines[1] == ("::warning file=CLAUDE.md,line=12,title=SR040 env-var-unused"
                        "::MY_VAR is not used anywhere else")
    assert lines[2] == "::notice file=AGENTS.md,line=4,title=SR999 test-info::fyi"


def test_github_summary_line():
    res = result([ERR, WARN, README_ERR], files=["CLAUDE.md", "README.md"], checked=7)
    last = report.github(res).splitlines()[-1]
    assert last == "amagi-staleref: 7 references in 2 files, 2 errors, 1 warnings"


def test_github_no_findings_is_summary_only():
    out = report.github(result(files=["CLAUDE.md"], checked=2))
    assert out == "amagi-staleref: 2 references in 1 files, 0 errors, 0 warnings"


def test_github_escapes_message():
    f = Finding("CLAUDE.md", 1, "SR001", "100% gone\r\nsecond line", "x")
    line = report.github(result([f])).splitlines()[0]
    assert line.endswith("::100%25 gone%0D%0Asecond line")


def test_gh_escape_percent_first():
    # "%0A" typed literally must not turn into a newline after escaping.
    assert report._gh_escape("%0A") == "%250A"
    assert report._gh_escape("") == ""


# ---------------------------------------------------------------- rules table
def test_rules_table_lists_every_rule_in_order():
    lines = report.rules_table().splitlines()
    assert len(lines) == len(RULES)
    assert [ln.split()[0] for ln in lines] == list(RULES)


def test_rules_table_row_format():
    first = report.rules_table().splitlines()[0]
    slug, sev, desc = RULES["SR001"]
    assert first == f"SR001  {sev:<7}  {slug:<24}  {desc}"


# ---------------------------------------------------------------- helpers
def test_plural():
    assert report._plural(0, "file") == "0 files"
    assert report._plural(1, "file") == "1 file"
    assert report._plural(2, "file") == "2 files"


def test_counts():
    assert report._counts([]) == {"error": 0, "warning": 0, "info": 0}
    assert report._counts([ERR, ERR, WARN]) == {"error": 2, "warning": 1, "info": 0}
