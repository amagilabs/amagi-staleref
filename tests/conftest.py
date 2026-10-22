import os
import shutil
import subprocess
import textwrap

import pytest

from amagi_staleref.cli import scan

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "fixtures", "demo_repo")
HAS_GIT = shutil.which("git") is not None


def write_tree(root, files):
    for rel, content in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(textwrap.dedent(content).lstrip("\n") if content else "")


def git_init(root):
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


@pytest.fixture
def make_repo(tmp_path):
    """make_repo({path: content}, git=False) -> repo root as str."""
    counter = {"n": 0}

    def _make(files, git=False):
        counter["n"] += 1
        root = tmp_path / f"repo{counter['n']}"
        root.mkdir()
        write_tree(str(root), files)
        if git:
            if not HAS_GIT:
                pytest.skip("git is not installed")
            git_init(str(root))
        return str(root)

    return _make


def findings(root, **kw):
    """Scan and return a sorted list of (file, line, rule, ref)."""
    result = scan(root, **kw)
    return sorted((f.file, f.line, f.rule, f.ref) for f in result["findings"])


def rules(root, **kw):
    return sorted({f[2] for f in findings(root, **kw)})
