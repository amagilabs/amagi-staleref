import os

import pytest

from amagi_staleref.repo import Repo, expand_braces, glob_to_regex

from conftest import HAS_GIT, write_tree


def test_make_targets_includes_phony_patterns_and_dynamic(make_repo):
    root = make_repo({
        "Makefile": (
            "CC := gcc\n"
            ".PHONY: test lint\n"
            "include mk/extra.mk\n"
            "build: deps\n"
            "\tgo build\n"
            "%.o: %.c\n"
            "\t$(CC) -c $<\n"
            "$(BIN): build\n"
            "define BLOCK\n"
            "fake: target\n"
            "endef\n"
        ),
        "mk/extra.mk": "migrate:\n\t./migrate\n",
    })
    targets, patterns, dynamic = Repo(root).make_targets("Makefile")
    assert targets == {"test", "lint", "build", "migrate"}
    assert patterns == {"%.o"}
    assert dynamic is True


def test_just_recipes_alias_mod_import_fallback(make_repo):
    root = make_repo({
        "justfile": (
            "set fallback\n"
            "import 'common.just'\n"
            "mod docs\n"
            "alias b := build\n"
            "build target='x':\n"
            "    cargo build\n"
            "@quiet:\n"
            "    true\n"
            "VERSION := '1'\n"
        ),
        "common.just": "fmt:\n    cargo fmt\n",
    })
    recipes, modules, fallback = Repo(root).just_recipes("justfile")
    assert recipes == {"b", "build", "quiet", "fmt"}
    assert modules == {"docs"}
    assert fallback is True


def test_task_names(make_repo):
    root = make_repo({
        "Taskfile.yml": (
            "version: '3'\n"
            "includes:\n"
            "  docs: ./docs/Taskfile.yml\n"
            "tasks:\n"
            "  build:\n"
            "    aliases: [b, compile]\n"
            "    cmds:\n"
            "      - go build\n"
            "  'db:migrate':\n"
            "    cmds: [echo]\n"
        ),
    })
    tasks, includes = Repo(root).task_names("Taskfile.yml")
    assert tasks == {"build", "b", "compile", "db:migrate"}
    assert includes == {"docs"}


def test_listing_without_git_skips_build_folders(make_repo):
    root = make_repo({"src/a.py": "", "node_modules/x/index.js": "", "README.md": ""})
    repo = Repo(root)
    assert not repo.git
    assert repo.is_file("src/a.py") and repo.is_dir("src")
    assert not any(f.startswith("node_modules/") for f in repo.files)


def test_existence_is_case_exact(make_repo):
    repo = Repo(make_repo({"Makefile": "x:\n"}))
    assert repo.is_file("Makefile")
    assert not repo.is_file("makefile")


def test_gitignore_without_git(make_repo):
    repo = Repo(make_repo({".gitignore": "dist/\n*.log\n", "README.md": ""}))
    assert repo.is_ignored("dist/app.js")
    assert repo.is_ignored("logs/run.log")
    assert not repo.is_ignored("src/app.js")


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_git_listing_and_ignore(make_repo):
    root = make_repo({".gitignore": "build/\n", "src/a.py": "", "README.md": ""}, git=True)
    write_tree(root, {"build/out.js": "", "new.txt": ""})
    repo = Repo(root)
    assert repo.git
    assert repo.is_file("new.txt")             # untracked files count
    assert "build/out.js" not in repo.files    # ignored files are not listed
    assert repo.is_ignored("build/out.js")
    assert repo.is_ignored("build/never-made.js")
    assert not repo.is_ignored("src/a.py")
    repo.close()


@pytest.mark.parametrize("git", [False, True])
def test_env_files_skip_real_env_but_read_templates(make_repo, git):
    if git and not HAS_GIT:
        pytest.skip("git is not installed")
    root = make_repo({
        ".env": "SECRET_ONLY=1\n",
        ".env.example": "API_TOKEN=\n",
        "app.py": "import os\nos.environ['DB_URL']\n",
        "README.md": "",
    }, git=git)
    repo = Repo(root)
    hits = repo.env_files({"SECRET_ONLY", "API_TOKEN", "DB_URL", "NOWHERE"})
    assert hits["SECRET_ONLY"] == set()
    assert hits["API_TOKEN"] == {".env.example"}
    assert hits["DB_URL"] == {"app.py"}
    assert hits["NOWHERE"] == set()


def test_remote_slugs(make_repo):
    root = make_repo({"README.md": ""})
    os.makedirs(os.path.join(root, ".git"))
    with open(os.path.join(root, ".git", "config"), "w") as fh:
        fh.write('[remote "origin"]\n\turl = https://git.example.com/acme/widget.git\n')
    slugs = Repo(root).remote_slugs()
    assert {"acme/widget", "widget", os.path.basename(root)} <= slugs


def test_generated_dirs(make_repo):
    repo = Repo(make_repo({"README.md": ""}))
    assert repo.is_generated("dist/app.js")
    assert repo.is_generated("pkg.egg-info/PKG-INFO")
    assert not repo.is_generated("src/app.js")


def test_glob_helpers():
    assert expand_braces("src/*.{ts,tsx}") == ["src/*.ts", "src/*.tsx"]
    rx = glob_to_regex("src/**/*.py")
    assert rx.match("src/a/b/c.py") and rx.match("src/c.py")
    assert not rx.match("lib/c.py")
