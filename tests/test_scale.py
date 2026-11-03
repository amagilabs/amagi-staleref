"""Large trees: the listing, the hint indexes, --max-files and progress lines."""

import difflib
import random
import string
import time

import pytest

from amagi_staleref import cli
from amagi_staleref.checks import FileChecker
from amagi_staleref.cli import main, scan
from amagi_staleref.repo import Repo, TooManyFiles, close_match

from conftest import write_tree


# ------------------------------------------------------------- listing
def test_walk_honours_nested_gitignore(make_repo):
    root = make_repo({
        ".gitignore": "*.log\n!keep.log\n/top-only/\nnotes/*.tmp\n",
        "a.log": "", "keep.log": "", "src/b.log": "",
        "top-only/x.md": "", "sub/top-only/y.md": "",
        "notes/n.tmp": "", "notes/deep/n.tmp": "",
        "pkg/.gitignore": "data/\ngenerated.py\n",
        "pkg/data/big.csv": "", "pkg/generated.py": "", "pkg/real.py": "", "pkg/data.py": "",
        "README.md": "",
    })
    files = Repo(root).files
    assert "keep.log" in files                       # ! re-includes
    assert not {"a.log", "src/b.log"} & files        # unanchored: any depth
    assert "top-only/x.md" not in files              # leading / anchors to the root
    assert "sub/top-only/y.md" in files
    assert "notes/n.tmp" not in files                # middle / anchors too
    assert "notes/deep/n.tmp" in files
    assert not {"pkg/data/big.csv", "pkg/generated.py"} & files   # a nested .gitignore
    assert {"pkg/real.py", "pkg/data.py"} <= files   # data/ is for folders only


def test_walk_honours_nested_repo_info_exclude(make_repo):
    root = make_repo({"proj/.git/info/exclude": "secret_plan.md\n", "proj/secret_plan.md": "",
                      "proj/README.md": "", "secret_plan.md": ""})
    files = Repo(root).files
    assert "proj/secret_plan.md" not in files
    assert "secret_plan.md" in files                 # the exclude file covers its own repo only
    assert not any(f.startswith("proj/.git") for f in files)


def test_walk_skips_venvs_caches_and_browser_profiles(make_repo):
    root = make_repo({
        "tools/benchy-venv/pyvenv.cfg": "home = x\n", "tools/benchy-venv/Lib/site.py": "",
        "tools/_profile/Local State": "{}", "tools/_profile/Default/Preferences": "{}",
        "cache/CACHEDIR.TAG": "a cache folder\n", "cache/blob": "",
        "tools/run.py": "", "README.md": "",
    })
    repo = Repo(root)
    assert repo.files == {"tools/run.py", "README.md"}
    assert sorted(repo.skipped) == ["cache", "tools/_profile", "tools/benchy-venv"]


def test_walk_skips_a_worktree_git_file(make_repo):
    root = make_repo({".git": "gitdir: /elsewhere/.git/worktrees/x\n", "README.md": ""})
    # a .git file is not a repo git understands here, so this is the plain walk
    assert ".git" not in Repo(root).files


def test_gitignore_negation_without_git(make_repo):
    repo = Repo(make_repo({".gitignore": "*.log\n!keep.log\n", "README.md": ""}))
    assert repo.is_ignored("logs/run.log")
    assert not repo.is_ignored("keep.log")
    assert not repo.is_ignored("logs/keep.log")


# ------------------------------------------------------------- --max-files
def test_max_files_stops_the_listing(make_repo):
    root = make_repo({f"f{i}.txt": "" for i in range(12)} | {"README.md": ""})
    with pytest.raises(TooManyFiles):
        Repo(root, max_files=10)
    assert len(Repo(root, max_files=0).files) == 13
    assert len(Repo(root, max_files=13).files) == 13


def test_cli_max_files(make_repo, capsys):
    root = make_repo({f"f{i}.txt": "" for i in range(12)} | {"README.md": "Run `src/app.py`.\n"})
    assert main([root, "--max-files", "5"]) == 2
    err = capsys.readouterr().err
    assert "more than 5 files" in err and "--max-files 0" in err
    assert main([root, "--max-files", "0"]) == 1     # scans, finds the stale path


def test_max_files_from_config(make_repo):
    root = make_repo({f"f{i}.txt": "" for i in range(12)} | {
        "README.md": "", ".amagi-staleref.toml": "max-files = 3\n"})
    with pytest.raises(TooManyFiles):
        scan(root)


# ------------------------------------------------------------- progress
def test_progress_lines_on_a_big_tree(make_repo, monkeypatch):
    root = make_repo({"README.md": "Run `src/app.py`.\n", "src/main.py": ""})
    monkeypatch.setattr(cli, "PROGRESS_FILES", 1)
    lines = []
    scan(root, progress=lines.append)
    assert lines[0].startswith("listed 2 files in ")
    assert lines[0].endswith("checking 1 instruction files")
    assert lines[-1] == "searching the repo for env var names"


def test_no_progress_on_a_small_tree(make_repo):
    lines = []
    scan(make_repo({"README.md": "Run `src/app.py`.\n"}), progress=lines.append)
    assert lines == []


def test_cli_progress_goes_to_stderr_and_can_be_turned_off(make_repo, capsys, monkeypatch):
    root = make_repo({"README.md": "Run `src/main.py`.\n", "src/main.py": ""})
    monkeypatch.setattr(cli, "PROGRESS_FILES", 1)
    main([root, "--format", "json"])
    out, err = capsys.readouterr()
    assert "amagi-staleref: listed" in err and "listed" not in out
    main([root, "--format", "json", "--no-progress"])
    assert capsys.readouterr().err == ""
    main([root, "--quiet"])
    assert capsys.readouterr().err == ""


# ------------------------------------------------------------- hints
def test_close_match_agrees_with_difflib():
    rng = random.Random(7)
    words = ["".join(rng.choice(string.ascii_lowercase[:6]) for _ in range(rng.randint(3, 9))) for _ in range(400)]
    for word in words[:60]:
        pool = sorted(set(words) - {word})
        for cutoff in (0.6, 0.75, 0.85):
            want = difflib.get_close_matches(word, pool, n=1, cutoff=cutoff)
            assert close_match(word, pool, cutoff) == (want[0] if want else None)


def test_close_file_name_agrees_with_difflib(tmp_path):
    rng = random.Random(11)
    chars = string.ascii_lowercase[:8] + "._-" + "éũ"  # two non-ASCII that fold onto ASCII bits
    names = {"".join(rng.choice(chars) for _ in range(rng.randint(2, 14))) for _ in range(1200)}
    repo = Repo(str(tmp_path))
    for i, n in enumerate(sorted(names)):
        repo._add(f"d{i % 40}/{n}")
    every = sorted(names)
    for word in [rng.choice(every) + rng.choice(chars) for _ in range(40)] + every[:20]:
        for cutoff in (0.6, 0.85):
            want = difflib.get_close_matches(word, every, n=1, cutoff=cutoff)
            assert repo.close_file_name(word, cutoff) == (want[0] if want else None), word


def _big_repo(tmp_path, n):
    """A Repo whose listing is n synthetic files (no files on disk)."""
    repo = Repo(str(tmp_path))
    for i in range(n):
        repo._add(f"pkg{i % 300}/mod{i % 97}/file_{i}.py")
    repo._add("docs/setup-guide.md")
    repo._add("src/app/config.py")
    for f in repo.files:
        repo._by_name.setdefault(f.rsplit("/", 1)[-1].lower(), []).append(f)
    return repo


def test_hints_on_a_large_tree_are_fast_and_right(tmp_path):
    repo = _big_repo(tmp_path, 60000)
    doc = "\n".join(
        [f"See `pkg1/mod1/missing_{i}.py` for details." for i in range(150)]
        + ["Read [the guide](docs/setup-guid.md).", "Edit `src/app/confg.py`.", "See `docs/setup-guide.mdx`."]
    ) + "\n"
    t = time.monotonic()
    checker = FileChecker(repo, "README.md", instruction_files={"README.md"})
    found = checker.run(doc)
    took = time.monotonic() - t
    msgs = {f.ref: f.message for f in found}
    assert len(found) == 153
    assert msgs["docs/setup-guid.md"].endswith("did you mean docs/setup-guide.md?")
    assert msgs["src/app/confg.py"].endswith("did you mean src/app/config.py?")
    assert msgs["docs/setup-guide.mdx"].endswith("did you mean docs/setup-guide.md?")
    assert took < 20, f"{took:.1f}s for 153 stale refs in a 60k-file tree"


def test_suffix_match_and_children_indexes(make_repo):
    repo = Repo(make_repo({"a/b/c.py": "", "x/b/c.py": "", "b/c.py": "", "a/bb/c.py": "", "README.md": ""}))
    assert sorted(repo.suffix_match("b/c.py")) == ["a/b/c.py", "x/b/c.py"]
    assert repo.children("a") == {"b", "bb"}
    assert repo.children("") == {"a", "x", "b", "README.md"}
    assert repo.children("nope") == set()
    assert sorted(repo.glob("c.py")) == ["a/b/c.py", "a/bb/c.py", "b/c.py", "x/b/c.py"]
