"""One small repo per rule: the stale reference is found, the live one is not."""

import json

import pytest

from conftest import findings, rules

PKG = json.dumps({"name": "web", "scripts": {"build": "x", "test": "x", "lint": "x"},
                  "devDependencies": {"eslint": "1"}})


def test_sr001_path_missing(make_repo):
    root = make_repo({
        "AGENTS.md": "Routes live in `src/app/routes.py`, models in `src/app/models.py`.\n",
        "src/app/routes.py": "",
    })
    assert findings(root) == [("AGENTS.md", 1, "SR001", "src/app/models.py")]


def test_sr001_hint_points_at_moved_file(make_repo):
    root = make_repo({"CLAUDE.md": "See `src/routes.py`.\n", "src/api/routes.py": ""})
    scan = __import__("amagi_staleref.cli", fromlist=["scan"]).scan
    msg = [f.message for f in scan(root)["findings"]]
    assert msg == ["'src/routes.py' does not exist; a file with that name is at src/api/routes.py"]


def test_sr001_relative_to_doc_folder(make_repo):
    root = make_repo({"pkg/AGENTS.md": "Edit `lib/core.py`.\n", "pkg/lib/core.py": ""})
    assert findings(root) == []


def test_sr001_skips_hypothetical_lines(make_repo):
    root = make_repo({"AGENTS.md": (
        "Never add files to `legacy/old.py`.\n"
        "Create `src/new_module.py` for the feature.\n"
        "This used to be `src/former.py`.\n"
    )})
    assert findings(root) == []


def test_sr001_hypothetical_word_carries_only_into_the_same_sentence(make_repo):
    root = make_repo({"AGENTS.md": (
        "Create `config/local.yml` with your settings.\n"
        "See `config/other.yml`.\n"
        "\n"
        "Never edit\n"
        "`src/generated.py` by hand. This lives in\n"
        "the vendor repo (`pkg/upstream.py`).\n"
    )})
    assert findings(root) == [("AGENTS.md", 2, "SR001", "config/other.yml")]


def test_sr001_skips_paths_outside_repo_and_system_paths(make_repo):
    root = make_repo({"a/AGENTS.md": "Sibling `../../other-repo/x.py` and `../../etc/passwd`.\n"})
    assert findings(root) == []


def test_sr002_off_by_default(make_repo):
    root = make_repo({"AGENTS.md": "Edit `settings.toml`.\n"})
    assert findings(root) == []
    assert findings(root, enable=["SR002"]) == [("AGENTS.md", 1, "SR002", "settings.toml")]


def test_sr002_found_anywhere(make_repo):
    root = make_repo({"AGENTS.md": "Edit `settings.toml`.\n", "deep/er/settings.toml": ""})
    assert findings(root, enable=["SR002"]) == []


def test_sr003_glob_empty(make_repo):
    root = make_repo({"AGENTS.md": "Tests are `tests/**/test_*.py`;"
                                   " components are `src/**/*.vue`.\n",
                      "tests/unit/test_a.py": "", "src/main.ts": ""})
    assert findings(root) == [("AGENTS.md", 1, "SR003", "src/**/*.vue")]


def test_glob_under_missing_folder_is_not_about_this_repo(make_repo):
    root = make_repo({"AGENTS.md": "Payloads look like `payloads/*.json`.\n"})
    assert findings(root) == []


def test_sr004_import_missing(make_repo):
    root = make_repo({"CLAUDE.md": "@docs/arch.md\n@docs/gone.md\n", "docs/arch.md": ""})
    assert findings(root) == [("CLAUDE.md", 2, "SR004", "docs/gone.md")]


def test_sr004_only_in_claude_files(make_repo):
    root = make_repo({"AGENTS.md": "Ping @docs/gone.md for help.\n"})
    assert findings(root) == []


def test_sr005_link_missing(make_repo):
    root = make_repo({"docs/README.md": "[ok](guide.md) [gone](setup.md)"
                                        " [web](https://x.dev) [a](#top)\n",
                      "docs/guide.md": ""})
    assert findings(root) == [("docs/README.md", 1, "SR005", "setup.md")]


def test_sr010_script_missing_with_hint(make_repo):
    root = make_repo({"AGENTS.md": "Run `npm run lint:fix` then `npm run build`.\n",
                      "package.json": PKG})
    result = __import__("amagi_staleref.cli", fromlist=["scan"]).scan(root)
    assert [(f.rule, f.ref) for f in result["findings"]] == [("SR010", "lint:fix")]
    assert "did you mean 'lint'?" in result["findings"][0].message


def test_sr010_builtins_and_bins_are_fine(make_repo):
    root = make_repo({"AGENTS.md": "```sh\nnpm ci\nnpm test\npnpm install\nnpx eslint .\n"
                                   "yarn eslint\n```\n",
                      "package.json": PKG})
    assert findings(root) == []


def test_sr011_no_package_json(make_repo):
    root = make_repo({"AGENTS.md": "`npm run build`\n"})
    assert rules(root) == ["SR011"]


def test_sr012_yarn_unknown(make_repo):
    root = make_repo({"AGENTS.md": "`yarn typecheck`\n", "package.json": PKG})
    assert findings(root) == [("AGENTS.md", 1, "SR012", "typecheck")]


def test_script_in_root_package_from_subfolder_doc(make_repo):
    root = make_repo({"packages/ui/AGENTS.md": "`bun run lint`\n", "packages/ui/package.json": "{}",
                      "package.json": PKG})
    assert findings(root) == []


MAKEFILE = "include extra.mk\n.PHONY: test lint\ntest:\n\tpytest\nbuild-%:\n\techo $@\nVAR := 1\n"


def test_sr020_make_target(make_repo):
    root = make_repo({"AGENTS.md": "```sh\nmake test\nmake build-web\nmake extra\n"
                                   "make migrate\n```\n",
                      "Makefile": MAKEFILE, "extra.mk": "extra:\n\ttrue\n"})
    assert findings(root) == [("AGENTS.md", 5, "SR020", "migrate")]


def test_sr021_no_makefile(make_repo):
    root = make_repo({"AGENTS.md": "`make test`\n"})
    assert rules(root) == ["SR021"]


def test_make_in_package_folder(make_repo):
    root = make_repo({"AGENTS.md": "Inside each service run `make test`.\n",
                      "svc/a/Makefile": "test:\n\ttrue\n"})
    assert findings(root) == []


def test_make_target_family_is_skipped(make_repo):
    root = make_repo({"AGENTS.md": "`make examples-*`\n", "Makefile": "examples-run:\n\ttrue\n"})
    assert findings(root) == []


def test_sr022_just_recipe(make_repo):
    root = make_repo({"AGENTS.md": "```sh\njust test\njust t\njust ui::dev\njust deploy\n```\n",
                      "justfile": "set shell := [\"bash\"]\nalias t := test\nmod ui\n"
                                  "@test arg='x':\n  echo\n"})
    assert findings(root) == [("AGENTS.md", 5, "SR022", "deploy")]


def test_sr023_no_justfile(make_repo):
    root = make_repo({"AGENTS.md": "`just test`\n"})
    assert rules(root) == ["SR023"]


def test_sr024_task(make_repo):
    taskfile = ("version: '3'\nincludes:\n  docs: ./docs\ntasks:\n  build:\n    aliases: [b]\n"
                "    cmds: [go build]\n")
    root = make_repo({"AGENTS.md": "```sh\ntask build\ntask b\ntask docs:serve\n"
                                   "task release\n```\n",
                      "Taskfile.yml": taskfile})
    assert findings(root) == [("AGENTS.md", 5, "SR024", "release")]


def test_sr025_no_taskfile(make_repo):
    root = make_repo({"AGENTS.md": "`task build`\n"})
    assert rules(root) == ["SR025"]


def test_sr030_sr031_python_m(make_repo):
    root = make_repo({
        "AGENTS.md": "```sh\npython -m app.server\npython -m app\npython -m app.worker\n"
                     "python -m pytest\npython -m tool\n```\n",
        "pyproject.toml": "[project]\nname = 'app'\n",
        "src/app/__init__.py": "", "src/app/__main__.py": "", "src/app/server.py": "",
        "src/tool/__init__.py": "",
    })
    assert findings(root) == [("AGENTS.md", 4, "SR030", "app.worker"),
                              ("AGENTS.md", 6, "SR031", "tool")]


def test_sr032_uv_run(make_repo):
    pyproject = ("[project]\nname = 'app'\ndependencies = ['uvicorn>=0.3']\n[project.scripts]\n"
                 "app-cli = 'app:main'\n[tool.hatch.metadata.hooks.x]\ndependencies = ['modal']\n")
    root = make_repo({
        "AGENTS.md": "```sh\nuv run app-cli\nuv run uvicorn app:api\nuv run modal deploy\n"
                     "uv run cargo build\nuv run -m app\nuv run notathing\n```\n",
        "pyproject.toml": pyproject, "src/app/__init__.py": "", "src/app/__main__.py": "",
    })
    assert findings(root) == [("AGENTS.md", 7, "SR032", "notathing")]


def test_sr040_env_var_non_git(make_repo):
    root = make_repo({"AGENTS.md": "Env vars: `DATABASE_URL` and `CACHE_URL`.\n",
                      "app/config.py": "import os\nURL = os.environ['DATABASE_URL']\n"})
    assert findings(root) == [("AGENTS.md", 1, "SR040", "CACHE_URL")]


def test_sr040_bare_name_needs_env_context(make_repo):
    root = make_repo({"AGENTS.md": "The `MAX_RETRIES` constant.\n"})
    assert findings(root) == []


def test_sr040_env_var_with_git(make_repo):
    root = make_repo({"AGENTS.md": "Env vars: `DATABASE_URL` and `CACHE_URL`.\n",
                      "app/config.py": "import os\nURL = os.environ['DATABASE_URL']\n"}, git=True)
    assert findings(root) == [("AGENTS.md", 1, "SR040", "CACHE_URL")]


def test_sr040_ignores_real_env_but_reads_templates(make_repo):
    root = make_repo({"AGENTS.md": "Set `API_TOKEN` and `SECRET_ONLY`.\n",
                      ".env.example": "API_TOKEN=\n", ".env": "SECRET_ONLY=1\n"})
    assert findings(root) == [("AGENTS.md", 1, "SR040", "SECRET_ONLY")]


def test_sr040_doc_local_shell_variable(make_repo):
    root = make_repo({"README.md": "```sh\n: \"${APP_BUNDLE:?set it}\"\n"
                                   "cp \"$APP_BUNDLE/x\" .\n```\n"})
    assert findings(root) == []


def test_sr040_reported_once_per_file(make_repo):
    root = make_repo({"AGENTS.md": "env: `CACHE_URL`\n\nthe `CACHE_URL` env var again\n"})
    assert findings(root) == [("AGENTS.md", 1, "SR040", "CACHE_URL")]


def test_sr050_cd_missing_skips_rest_of_block(make_repo):
    root = make_repo({"AGENTS.md": "```sh\ncd frontend\nnpm run nope\n```\n"})
    assert findings(root) == [("AGENTS.md", 2, "SR050", "frontend")]


def test_cd_moves_cwd(make_repo):
    root = make_repo({"AGENTS.md": "```sh\ncd web && npm run build\nmake test\n```\n",
                      "web/package.json": PKG, "web/Makefile": "test:\n\ttrue\n"})
    assert findings(root) == []


def test_cd_root_relative_and_clone_name(make_repo):
    root = make_repo({
        "examples/app/README.md": "```sh\ncd examples/app\nmake run\n```\n",
        "examples/app/Makefile": "run:\n\ttrue\n",
        "README.md": "```sh\ngit clone https://example.invalid/x.git\ncd placeholder-name\n```\n",
    })
    assert ("examples/app/README.md", 2, "SR050", "examples/app") not in findings(root)


def test_cd_after_mkdir_and_placeholders(make_repo):
    root = make_repo({"README.md": "```sh\nmkdir demo && cd demo\nmake x\n```\n\n"
                                   "```sh\ncd my-app\n```\n"})
    assert findings(root) == []


def test_ignore_comment_and_disable(make_repo):
    root = make_repo({"AGENTS.md": (
        "<!-- staleref-disable SR040 -->\n"
        "`gone/a.py` <!-- staleref-ignore -->\n"
        "env: `CACHE_URL`\n"
        "`gone/b.py`\n"
    )})
    assert findings(root) == [("AGENTS.md", 4, "SR001", "gone/b.py")]


@pytest.mark.parametrize("name", ["agents.md", "claude.md", "Agents.md"])
def test_instruction_names_are_case_sensitive(make_repo, name):
    root = make_repo({f"docs/{name}": "`gone/file.py`\n"})
    assert scan_files(root) == []


def test_readme_names_are_case_insensitive(make_repo):
    root = make_repo({"pkg/Readme.md": "`gone/file.py`\n", "docs/rules.md": "`gone/x.py`\n"})
    assert scan_files(root) == ["pkg/Readme.md"]
    assert scan_files(root, readme=False) == []


def test_rule_folders_are_scanned(make_repo):
    root = make_repo({".cursor/rules/style.mdc": "x\n", ".github/copilot-instructions.md": "x\n",
                      "CLAUDE.local.md": "x\n"})
    assert sorted(scan_files(root)) == [".cursor/rules/style.mdc",
                                         ".github/copilot-instructions.md", "CLAUDE.local.md"]


def scan_files(root, **kw):
    from amagi_staleref.cli import scan
    return scan(root, **kw)["files"]
