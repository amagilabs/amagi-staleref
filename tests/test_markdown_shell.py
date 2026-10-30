from amagi_staleref import markdown, shell


def test_extract_spans_blocks_links_imports():
    text = (
        "---\n"
        "title: `not/checked.md`\n"
        "---\n"
        "# Notes\n"
        "Run `npm test` and read [the guide](docs/guide.md \"Guide\").\n"
        "@docs/arch.md\n"
        "\n"
        "```bash\n"
        "make build\n"
        "```\n"
        "[ref]: docs/ref.md\n"
        "[^1]: is a footnote\n"
    )
    ex = markdown.extract(text, imports=True)
    assert [(s.line, s.text) for s in ex.spans] == [(5, "npm test")]
    assert [(s.line, s.text) for s in ex.links] == [(5, "docs/guide.md"), (11, "docs/ref.md")]
    assert [(s.line, s.text) for s in ex.imports] == [(6, "docs/arch.md")]
    assert len(ex.blocks) == 1
    assert ex.blocks[0].lang == "bash"
    assert ex.blocks[0].lines == [(9, "make build")]


def test_imports_only_when_asked():
    assert markdown.extract("@docs/arch.md\n").imports == []


def test_external_link_text_is_not_a_span():
    ex = markdown.extract("* [`numpy/globals`](https://git.example.com/numpy/numpy/blob/main/numpy/_globals.py)\n")
    assert ex.spans == []


def test_double_backtick_span():
    ex = markdown.extract("Use `` a`b `` here\n")
    assert [s.text for s in ex.spans] == ["a`b"]


def test_ignore_marks_and_disable():
    text = (
        "<!-- staleref-disable SR040, SR002 -->\n"
        "`old/path.py` <!-- staleref-ignore -->\n"
        "<!-- staleref-ignore -->\n"
        "`next/line.py`\n"
        "`checked/path.py`\n"
    )
    ex = markdown.extract(text)
    assert ex.disabled == {"SR040", "SR002"}
    assert ex.ignored_lines == {2, 3, 4}


def test_ignored_block():
    text = "<!-- staleref-ignore -->\n```sh\nmake nope\n```\n"
    assert markdown.extract(text).blocks == []


def test_unclosed_fence_runs_to_end():
    ex = markdown.extract("```sh\nmake a\nmake b\n")
    assert [t for _, t in ex.blocks[0].lines] == ["make a", "make b"]


# ------------------------------------------------------------------ shell

def argvs(lines, lang="bash"):
    return [c.argv for c in shell.block_commands(list(enumerate(lines, 1)), lang)]


def test_split_operators_and_redirects():
    cmds = shell.split_commands("cd app && npm ci 2>/dev/null | tee log.txt; make test", 3)
    assert [c.argv for c in cmds] == [["cd", "app"], ["npm", "ci"], ["tee", "log.txt"],
                                      ["make", "test"]]
    assert all(c.line == 3 for c in cmds)


def test_assignments_and_wrappers():
    (cmd,) = shell.split_commands("FOO=1 sudo env BAR=2 python -m app")
    assert cmd.argv == ["python", "-m", "app"]
    assert cmd.assigns == [("FOO", "1"), ("BAR", "2")]


def test_prompts_continuations_and_heredocs():
    lines = [
        "$ npm run build \\",
        "    --prod",
        "output line that is not a command",
        "$ cat <<EOF > x.txt",
    ]
    assert argvs(lines) == [["npm", "run", "build", "--prod"], ["cat"]]
    heredoc = ["cat <<EOF", "make nope", "EOF", "make yes"]
    assert argvs(heredoc) == [["cat"], ["make", "yes"]]


def test_session_blocks_skip_output():
    assert argvs(["$ make test", "ok 3 passed"], lang="console") == [["make", "test"]]


def test_powershell():
    lines = ["$env:API_URL = 'http://x'", "python .\\scripts\\seed.py `", "  --fast"]
    cmds = shell.block_commands(list(enumerate(lines, 1)), "powershell")
    assert cmds[0].assigns == [("API_URL", "http://x")]
    assert cmds[1].argv == ["python", "./scripts/seed.py", "--fast"]


def test_non_shell_blocks_and_trees_are_skipped():
    assert argvs(["make test"], lang="python") == []
    assert argvs(["├── src/", "└── make.py"]) == []
