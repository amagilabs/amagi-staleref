import os

import pytest

from amagi_staleref.discover import (
    INSTRUCTION_NAMES, LOCAL_ONLY, README_NAMES, RULE_DIRS, find_instruction_files, is_instruction,
)
from amagi_staleref.repo import Repo

from conftest import HAS_GIT, git_init, write_tree


# ---------------------------------------------------------------- is_instruction
@pytest.mark.parametrize("name", sorted(INSTRUCTION_NAMES))
def test_agent_file_names_at_root_and_nested(name):
    assert is_instruction(name)
    assert is_instruction(f"packages/web/{name}")


@pytest.mark.parametrize("rel", ["agents.md", "docs/agents.md", "claude.md", "Claude.md", "AGENTS.MD"])
def test_agent_file_names_are_case_sensitive(rel):
    assert not is_instruction(rel)


@pytest.mark.parametrize("rel", ["README.md", "readme.md", "ReadMe.markdown", "docs/README.mdx", "README", "pkg/readme.txt"])
def test_readmes_case_insensitive(rel):
    assert is_instruction(rel)
    assert not is_instruction(rel, readme=False)


def test_readme_flag_does_not_affect_agent_files():
    assert is_instruction("CLAUDE.md", readme=False)


@pytest.mark.parametrize("rel", ["README.rst", "readme.html", "README.md.bak", "notes/README-old.md", "CHANGELOG.md"])
def test_non_matching_names(rel):
    assert not is_instruction(rel)


@pytest.mark.parametrize("d", RULE_DIRS)
def test_rule_dirs_md_mdc_txt(d):
    for ext in (".md", ".mdc", ".txt"):
        assert is_instruction(f"{d}style{ext}")
        assert is_instruction(f"apps/api/{d}style{ext}")
    assert not is_instruction(f"{d}logo.png")
    assert not is_instruction(f"{d}config.json")


def test_rule_dir_extension_is_case_insensitive():
    assert is_instruction(".cursor/rules/Style.MDC")


def test_rule_dir_needs_a_path_boundary():
    # "x.cursor/rules/" is not ".cursor/rules/" nested in a folder.
    assert not is_instruction("x.cursor/rules/style.md")
    assert not is_instruction("docs/cursor/rules/style.md")


def test_rule_dir_readme_off_still_matches_rule_files():
    assert is_instruction(".github/instructions/python.md", readme=False)


# ---------------------------------------------------------------- find_instruction_files
@pytest.fixture
def tree(tmp_path):
    def _make(files):
        write_tree(str(tmp_path), files)
        return Repo(str(tmp_path))
    return _make


def test_find_orders_root_first_agents_before_readmes(tree):
    repo = tree({
        "README.md": "x", "CLAUDE.md": "x", "AGENTS.md": "x",
        "docs/README.md": "x", "docs/CLAUDE.md": "x",
        "a/b/AGENTS.md": "x", "src/main.py": "x", "docs/guide.md": "x",
    })
    assert find_instruction_files(repo) == [
        "AGENTS.md", "CLAUDE.md", "README.md",
        "docs/CLAUDE.md", "docs/README.md",
        "a/b/AGENTS.md",
    ]


def test_find_alphabetical_is_case_insensitive(tree):
    repo = tree({"CLAUDE.md": "x", "AGENTS.md": "x", ".cursorrules": "x", "GEMINI.md": "x"})
    assert find_instruction_files(repo) == [".cursorrules", "AGENTS.md", "CLAUDE.md", "GEMINI.md"]


def test_find_without_readmes(tree):
    repo = tree({"README.md": "x", "CLAUDE.md": "x", "docs/readme.txt": "x"})
    assert find_instruction_files(repo, readme=False) == ["CLAUDE.md"]


def test_find_include_globs(tree):
    repo = tree({"CLAUDE.md": "x", "docs/guide.md": "x", "docs/deep/more.md": "x", "docs/img.png": "x"})
    got = find_instruction_files(repo, include=["docs/**/*.md"])
    assert got == ["CLAUDE.md", "docs/guide.md", "docs/deep/more.md"]


def test_find_include_strips_slashes(tree):
    repo = tree({"docs/guide.md": "x"})
    assert find_instruction_files(repo, include=["/docs/*.md/"]) == ["docs/guide.md"]


def test_find_exclude_wins_over_default_and_include(tree):
    repo = tree({"CLAUDE.md": "x", "vendor/lib/README.md": "x", "docs/guide.md": "x"})
    got = find_instruction_files(repo, include=["docs/*.md"], exclude=["vendor/**", "docs/guide.md"])
    assert got == ["CLAUDE.md"]


def test_find_empty_repo(tree):
    assert find_instruction_files(tree({"src/app.py": "x"})) == []


def test_find_rule_dir_files(tree):
    repo = tree({".cursor/rules/a.mdc": "x", ".cursor/rules/b.json": "x", ".clinerules/x.md": "x"})
    assert find_instruction_files(repo) == [".clinerules/x.md", ".cursor/rules/a.mdc"]


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_find_picks_up_gitignored_local_only_files(tmp_path):
    root = str(tmp_path)
    files = {name: "x" for name in LOCAL_ONLY}
    files.update({"CLAUDE.md": "x", ".gitignore": "\n".join(LOCAL_ONLY) + "\n"})
    write_tree(root, files)
    git_init(root)
    repo = Repo(root)
    assert repo.git
    assert not any(name in repo.files for name in LOCAL_ONLY)
    got = find_instruction_files(repo)
    assert set(LOCAL_ONLY) <= set(got)
    assert "CLAUDE.md" in got


@pytest.mark.skipif(not HAS_GIT, reason="git is not installed")
def test_find_gitignored_local_only_respects_exclude(tmp_path):
    root = str(tmp_path)
    write_tree(root, {"CLAUDE.local.md": "x", ".gitignore": "CLAUDE.local.md\n"})
    git_init(root)
    repo = Repo(root)
    assert find_instruction_files(repo) == ["CLAUDE.local.md"]
    assert find_instruction_files(repo, exclude=["CLAUDE.local.md"]) == []


def test_find_local_only_folder_is_not_a_file(tmp_path):
    # A directory named .clinerules (rule folder) is not the single-file form.
    write_tree(str(tmp_path), {".clinerules/rules.md": "x"})
    repo = Repo(str(tmp_path))
    assert find_instruction_files(repo) == [".clinerules/rules.md"]
    assert os.path.isdir(os.path.join(str(tmp_path), ".clinerules"))


def test_readme_names_are_lowercase():
    # is_instruction lowercases the base name before checking README_NAMES.
    assert all(n == n.lower() for n in README_NAMES)
