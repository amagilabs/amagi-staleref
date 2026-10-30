"""The demo fixture is the README example; its output must not drift."""

import json

from amagi_staleref.cli import main

from conftest import DEMO

EXPECTED = """\
.cursorrules
     1  error    SR001  'src/components/' does not exist
     1  warning  SR003  glob 'src/**/*.tsx' matches no files

AGENTS.md
     3  warning  SR012  yarn script 'typecheck' is not a script, built-in or installed package in package.json; scripts: build, dev, lint, test
     8  warning  SR050  cd 'frontend': no such folder

CLAUDE.md
     4  error    SR004  @-import 'docs/runbook.md' does not exist
     9  error    SR010  npm script 'lint:fix' is not a script in package.json; did you mean 'lint'?
    11  error    SR020  make target 'migrate' is not defined in Makefile
    12  error    SR022  just recipe 'deploy' is not defined in justfile
    13  error    SR030  python -m app.worker: no src/app/worker.py or src/app/worker/ in the repo
    17  error    SR001  'scripts/backfill.py' does not exist
    22  error    SR001  'src/app/routes.py' does not exist; a file with that name is at src/app/api/routes.py
    23  warning  SR040  env var CACHE_URL is not mentioned anywhere else in the repo

Checked 19 references in 3 files. 12 stale: 8 errors, 4 warnings.
"""  # noqa: E501 - verbatim CLI output


def test_demo_text_output(capsys):
    assert main([DEMO]) == 1
    assert capsys.readouterr().out == EXPECTED


def test_demo_json(capsys):
    assert main([DEMO, "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["checked"] == 19
    assert data["files"] == [".cursorrules", "AGENTS.md", "CLAUDE.md"]
    assert data["summary"] == {"error": 8, "warning": 4, "info": 0}
    first = data["findings"][0]
    assert first == {"file": ".cursorrules", "line": 1, "rule": "SR001", "slug": "path-missing",
                     "severity": "error", "ref": "src/components/",
                     "message": "'src/components/' does not exist"}


def test_demo_github(capsys):
    assert main([DEMO, "--format", "github"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == ("::error file=.cursorrules,line=1,title=SR001 path-missing"
                      "::'src/components/' does not exist")
    assert out[1].startswith("::warning file=.cursorrules,line=1,title=SR003 glob-empty::")
    assert out[-1] == "amagi-staleref: 19 references in 3 files, 8 errors, 4 warnings"
    assert len(out) == 13


def test_demo_fail_on_error_and_never(capsys):
    assert main([DEMO, "--fail-on", "error"]) == 1
    assert main([DEMO, "--fail-on", "never"]) == 0
    assert main([DEMO, "--select", "SR050", "--fail-on", "error"]) == 0
    capsys.readouterr()


def test_demo_select_and_ignore(capsys):
    assert main([DEMO, "--select", "SR001", "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert {f["rule"] for f in data["findings"]} == {"SR001"}
    assert len(data["findings"]) == 3
    main([DEMO, "--ignore", "SR001,SR003", "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert not {f["rule"] for f in data["findings"]} & {"SR001", "SR003"}
    assert len(data["findings"]) == 8
