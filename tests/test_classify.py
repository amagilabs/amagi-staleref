import pytest

from amagi_staleref.checks import classify, clean_token, is_placeholder


@pytest.mark.parametrize("tok,kind", [
    ("src/app/routes.py", "path"),
    ("./scripts/seed.py", "path"),
    ("../shared/README.md", "path"),
    ("src/components/", "path"),
    ("docs/guides", "maybe"),
    ("package.json", "bare"),
    ("Makefile", "bare"),
    (".env", "bare"),
    (".github/workflows/ci.yml", "path"),
    ("src/**/*.tsx", "glob"),
    ("*.py", "glob"),
    ("docs/*", "glob"),
])
def test_classify_paths(tok, kind):
    assert classify(tok) == kind


@pytest.mark.parametrize("tok", [
    "https://example.com/a.md",       # URL
    "~/notes.md",                     # home folder
    "/etc/hosts",                     # absolute
    "@scope/pkg",                     # npm scope
    "node.js", "next.js",             # tech names
    "process.env", "import.meta.env",  # code
    "path/to/file.py",                # placeholder
    "your-app/config.yml",
    "adr_NNN_title.md",
    "foo.py", "my_script.py", "example.json",
    "lib/",                           # lone folder in prose
    "CODEX_HOME/config.toml",         # env var as a folder
    "Test*", "getBy*", "*_KEY",       # identifiers, not file globs
    "*/list",                         # MCP method pattern
    ".md",                            # an extension
    ".all",                           # a suffix
    "mappers/__init__.PRESETS",       # Python attribute
    "...", "---",
    "a b.py",                         # whitespace
])
def test_classify_skips(tok):
    assert classify(tok) is None


@pytest.mark.parametrize("raw,clean", [
    ("src/app.py:12", "src/app.py"),
    ("src/app.py:12-20", "src/app.py"),
    ("src/app.py#L5-L9", "src/app.py"),
    ("docs/guide.md#some-heading", "docs/guide.md"),
    ("tests/test_x.py::test_y", "tests/test_x.py"),
    ("'README.md'", "README.md"),
    ("src/app.py,", "src/app.py"),
])
def test_clean_token(raw, clean):
    assert clean_token(raw) == clean


@pytest.mark.parametrize("tok,expected", [
    ("path/to/thing", True),
    ("new-project/", True),
    ("hello-world", True),
    ("my-app", True),
    ("src/xxx/file.py", True),
    ("src/app", False),
    ("frontend", False),
])
def test_is_placeholder(tok, expected):
    assert is_placeholder(tok) is expected
