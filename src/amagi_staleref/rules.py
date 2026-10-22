"""Rule table: ID -> (slug, default severity, one-line description)."""

ERROR = "error"
WARNING = "warning"
INFO = "info"

SEVERITY_ORDER = {INFO: 0, WARNING: 1, ERROR: 2}

RULES = {
    "SR001": ("path-missing", ERROR, "A file or folder path that does not exist in the repo"),
    "SR002": ("file-missing", WARNING, "A bare file name (no folder) that matches no file anywhere in the repo (off unless --enable SR002)"),
    "SR003": ("glob-empty", WARNING, "A glob pattern that matches no files"),
    "SR004": ("import-missing", ERROR, "A CLAUDE.md @-import whose target file does not exist"),
    "SR005": ("link-missing", ERROR, "A relative Markdown link whose target does not exist"),
    "SR010": ("script-missing", ERROR, "npm/pnpm/yarn/bun run <name> where package.json has no such script"),
    "SR011": ("package-json-missing", ERROR, "An npm/pnpm/yarn/bun script command with no package.json to run it"),
    "SR012": ("script-or-bin-unknown", WARNING, "pnpm/yarn/bun <name> that is not a script, built-in or installed package"),
    "SR020": ("make-target-missing", ERROR, "make <target> where the Makefile defines no such target"),
    "SR021": ("makefile-missing", ERROR, "A make command with no Makefile to run it"),
    "SR022": ("just-recipe-missing", ERROR, "just <recipe> where the justfile defines no such recipe"),
    "SR023": ("justfile-missing", ERROR, "A just command with no justfile to run it"),
    "SR024": ("task-missing", ERROR, "task <name> where Taskfile.yml defines no such task"),
    "SR025": ("taskfile-missing", ERROR, "A task command with no Taskfile.yml to run it"),
    "SR030": ("python-module-missing", ERROR, "python -m <module> where the repo's own package has no such module"),
    "SR031": ("python-main-missing", ERROR, "python -m <package> where the package has no __main__.py"),
    "SR032": ("runner-target-unknown", WARNING, "uv/poetry/pdm/hatch/pipenv run <name> that is not a script or dependency"),
    "SR040": ("env-var-unused", WARNING, "An env var that nothing else in the repo mentions"),
    "SR050": ("cd-missing", WARNING, "cd into a folder that does not exist (the rest of that block is skipped)"),
}

# Off by default: bare file names in prose are often files in other repos, tools or
# build output, so these rules are noisier. Turn them on with --enable.
OPTIONAL = {"SR002"}


def severity_of(rule_id):
    return RULES[rule_id][1]


def slug_of(rule_id):
    return RULES[rule_id][0]
