import json
import os

import pytest

from amagi_staleref import __version__
from amagi_staleref.cli import main
from amagi_staleref.rules import RULES

CLEAN = {
    "CLAUDE.md": """
        # Notes
        Run `npm test`. Code lives in `src/app.js`.
        """,
    "package.json": '{"scripts": {"test": "node src/app.js"}}\n',
    "src/app.js": "console.log(1)\n",
}


def test_clean_repo_exits_0(make_repo, capsys):
    root = make_repo(CLEAN)
    assert main([root]) == 0
    assert capsys.readouterr().out.strip() == "Checked 2 references in 1 file. Nothing stale."


def test_quiet_prints_nothing_when_clean(make_repo, capsys):
    root = make_repo(CLEAN)
    assert main([root, "--quiet"]) == 0
    assert capsys.readouterr().out == ""


def test_quiet_still_prints_findings(make_repo, capsys):
    root = make_repo({"CLAUDE.md": "See `src/gone.py`.\n"})
    assert main([root, "--quiet"]) == 1
    assert "SR001" in capsys.readouterr().out


def test_unknown_rule_is_usage_error(make_repo, capsys):
    root = make_repo(CLEAN)
    assert main([root, "--ignore", "SR999"]) == 2
    assert "unknown rule 'SR999'" in capsys.readouterr().err


def test_missing_path_is_usage_error(tmp_path, capsys):
    assert main([str(tmp_path / "nope")]) == 2
    assert "no such file or folder" in capsys.readouterr().err


def test_no_instruction_files_is_exit_2(make_repo, capsys):
    root = make_repo({"src/app.py": "x = 1\n"})
    assert main([root]) == 2
    assert "no CLAUDE.md, AGENTS.md, .cursorrules or README found" in capsys.readouterr().out
    assert main([root, "--quiet"]) == 0


def test_list_rules(capsys):
    assert main(["--list-rules"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == len(RULES)
    assert out[0].startswith("SR001  error    path-missing")


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"amagi-staleref {__version__}"


def test_warning_only_and_fail_on(make_repo, capsys):
    root = make_repo({"CLAUDE.md": "Env vars: `CACHE_URL`.\n"})
    assert main([root]) == 1
    assert main([root, "--fail-on", "error"]) == 0
    capsys.readouterr()


def test_file_args_with_root(make_repo, capsys):
    root = make_repo({
        "CLAUDE.md": "See `src/gone.py`.\n",
        "AGENTS.md": "See `src/also_gone.py`.\n",
    })
    assert main([os.path.join(root, "AGENTS.md"), "--root", root, "--format", "json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["files"] == ["AGENTS.md"]
    assert [f["ref"] for f in data["findings"]] == ["src/also_gone.py"]


def test_file_outside_root_is_error(make_repo, tmp_path, capsys):
    root = make_repo({"CLAUDE.md": "hi\n"})
    other = make_repo({"AGENTS.md": "hi\n"})
    assert main([os.path.join(other, "AGENTS.md"), "--root", root]) == 2
    assert "outside the root" in capsys.readouterr().err


def test_two_folders_prefix_paths(make_repo, capsys):
    a = make_repo({"CLAUDE.md": "See `src/gone.py`.\n"})
    b = make_repo({"CLAUDE.md": "See `lib/gone.py`.\n"})
    assert main([a, b, "--format", "json"]) == 1
    files = json.loads(capsys.readouterr().out)["files"]
    assert len(files) == 2 and all(f.endswith("/CLAUDE.md") for f in files)


def test_no_readme(make_repo, capsys):
    root = make_repo({"README.md": "See `src/gone.py`.\n", "CLAUDE.md": "Hello.\n"})
    assert main([root]) == 1
    assert main([root, "--no-readme"]) == 0
    capsys.readouterr()


def test_include_and_exclude(make_repo, capsys):
    root = make_repo({
        "CLAUDE.md": "Hello.\n",
        "docs/agents/setup.md": "See `src/gone.py`.\n",
        "README.md": "See `src/gone.py`.\n",
    })
    assert main([root, "--exclude", "README.md"]) == 0
    assert main([root, "--exclude", "README.md", "--include", "docs/**/*.md"]) == 1
    capsys.readouterr()


def test_enable_optional_rule(make_repo, capsys):
    root = make_repo({"CLAUDE.md": "Edit `settings.yaml` first.\n"})
    assert main([root]) == 0
    assert main([root, "--enable", "SR002"]) == 1
    assert "SR002" in capsys.readouterr().out


def test_config_in_pyproject(make_repo, capsys):
    root = make_repo({
        "CLAUDE.md": "See `src/gone.py`. Env vars: `CACHE_URL`.\n",
        "pyproject.toml": '[tool.amagi-staleref]\nignore = ["SR001"]\nfail-on = "error"\n',
    })
    assert main([root, "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [f["rule"] for f in data["findings"]] == ["SR040"]


def test_config_file_exclude_and_readme(make_repo, capsys):
    root = make_repo({
        "CLAUDE.md": "Hello.\n",
        "README.md": "See `src/gone.py`.\n",
        "vendor/AGENTS.md": "See `lib/gone.py`.\n",
        ".amagi-staleref.toml": 'readme = false\nexclude = ["vendor/**"]\n',
    })
    assert main([root]) == 0
    capsys.readouterr()


def test_config_enable(make_repo, capsys):
    root = make_repo({
        "CLAUDE.md": "Edit `settings.yaml` first.\n",
        ".amagi-staleref.toml": 'enable = ["SR002"]\n',
    })
    assert main([root]) == 1
    capsys.readouterr()
