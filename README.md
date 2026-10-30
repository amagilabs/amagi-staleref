# amagi-staleref

Find the lines in CLAUDE.md, AGENTS.md, .cursorrules and README files that
point at files, scripts and commands your repo no longer has.

![demo](assets/demo.gif)

Instruction files rot without anyone noticing. A folder gets renamed or a
`make` target gets dropped, the docs still say to use it, and your coding
agent spends a turn running a command that fails. amagi-staleref reads those
files and checks every reference in them against the repo:

- file and folder paths, globs, relative links and CLAUDE.md `@` imports
- npm, pnpm, yarn and bun scripts, checked against package.json
- make targets, just recipes and Taskfile tasks
- `python -m` modules from the repo's own packages, and uv, poetry, pdm,
  hatch and pipenv `run` names
- `cd` into a folder that is gone
- env vars that no other file in the repo mentions

Each stale reference comes out with its file, line number and, when it can
find one, the likely new location. Run it by hand, as a pre-commit hook, or in
CI. It reads files and nothing else: no network, no LLM calls, no API keys.

## What it found in 10 public repos

We ran it on shallow clones of 10 open-source repos that ship agent
instruction files: codex, gemini-cli, langchain, litellm, openai-agents-python,
opencode, playwright-mcp, pydantic-ai, the MCP Python SDK and ruff.

- Agent files only (`--no-readme`): 879 references in 169 files, 6
  findings. We checked all 6 by hand and each one is real. Three are litellm
  AGENTS.md files that name test folders that are gone. The other three are
  in a pydantic-ai AGENTS.md that still points at test files from before they
  moved into tests/clai2.
- Agent files plus READMEs (the default): 3,012 references in 662 files,
  37 findings. We confirmed 19 by hand as files, links or recipes that moved
  or were renamed. The rest include output files the docs describe, paths
  inside another repo that a tutorial mounts, and a few we could not settle
  either way.

## Install

```
pipx install amagi-staleref
```

Python 3.9 or newer. No dependencies on Python 3.11 and up; older versions
pull in `tomli` to read `pyproject.toml`.

## Usage

```
amagi-staleref [PATH ...] [--root DIR] [--format text|json|github]
               [--fail-on error|warning|info|never] [--no-readme]
               [--include GLOB] [--exclude GLOB] [--ignore IDS] [--select IDS]
               [--enable IDS] [--quiet] [--list-rules]
```

With no PATH it scans the current folder. A PATH can be a repo folder, or
one or more files; files are resolved against `--root` (default: the current
folder).

It reads these files, wherever they sit in the repo:

- CLAUDE.md, CLAUDE.local.md, AGENTS.md, AGENT.md, GEMINI.md
- .cursorrules, .windsurfrules, .clinerules, copilot-instructions.md
- Markdown files under .cursor/rules, .clinerules, .windsurf/rules,
  .github/instructions and .roo/rules
- README files (skip them with `--no-readme`)

This is the real output on the demo fixture in this repo
(`tests/fixtures/demo_repo`):

```text
$ amagi-staleref
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
```

### Options

| Option | What it does |
|---|---|
| `--format` | `text` (default), `json`, or `github` for Actions annotations |
| `--fail-on` | Lowest severity that sets exit code 1: `error`, `warning` (default), `info`, `never` |
| `--no-readme` | Check agent instruction files only |
| `--include GLOB` | Also check files matching GLOB, e.g. `--include 'docs/**/*.md'` (repeatable) |
| `--exclude GLOB` | Skip files matching GLOB (repeatable) |
| `--ignore IDS` | Skip rules, e.g. `--ignore SR040,SR003` |
| `--select IDS` | Run only these rules |
| `--enable IDS` | Turn on rules that are off by default (SR002) |
| `--root DIR` | Repo root for resolving references when you pass files |
| `--quiet` | Print nothing when nothing is stale |
| `--list-rules` | Print every rule and exit |

Exit codes: 0 nothing at or above `--fail-on`, 1 stale references found, 2
usage error or no instruction files found.

### Skipping a line on purpose

Some docs name a path that does not exist yet, such as a file the reader
creates in step 2. End that line with a comment:

```
Create `config/local.yml` with your settings. <!-- staleref-ignore -->
```

A comment on a line of its own skips the next line. To turn rules off for a
whole file, put `<!-- staleref-disable SR040 -->` anywhere in it.

### Config

Put defaults in `pyproject.toml`:

```toml
[tool.amagi-staleref]
exclude = ["tests/fixtures/**"]
ignore = ["SR040"]
fail-on = "error"
```

Or use the same keys at the top level of `.amagi-staleref.toml`. Keys:
`include`, `exclude`, `ignore`, `select`, `enable`, `readme` (true or false)
and `fail-on`.

## Rules

| ID | Severity | Catches |
|---|---|---|
| SR001 | error | A file or folder path that does not exist in the repo |
| SR002 | warning | A bare file name that matches no file anywhere in the repo (off unless `--enable SR002`) |
| SR003 | warning | A glob that matches no files |
| SR004 | error | A CLAUDE.md `@` import whose target does not exist |
| SR005 | error | A relative Markdown link whose target does not exist |
| SR010 | error | `npm run`, `pnpm run`, `yarn run` or `bun run` a script package.json does not define |
| SR011 | error | A package script command with no package.json to run it |
| SR012 | warning | pnpm, yarn or bun shorthand that is not a script, built-in or installed package |
| SR020 | error | A make target the Makefile does not define |
| SR021 | error | A make command with no Makefile |
| SR022 | error | A just recipe the justfile does not define |
| SR023 | error | A just command with no justfile |
| SR024 | error | A task the Taskfile does not define |
| SR025 | error | A task command with no Taskfile |
| SR030 | error | `python -m` a module the repo's own package does not have |
| SR031 | error | `python -m` a package with no `__main__.py` |
| SR032 | warning | uv, poetry, pdm, hatch or pipenv `run` a name that is not a script or dependency |
| SR040 | warning | An env var that nothing else in the repo mentions |
| SR050 | warning | `cd` into a folder that does not exist (the rest of that code block is skipped) |

### How it avoids false alarms

Instruction files are full of paths that are not meant to exist in your
repo. It skips:

- URLs, absolute paths, home-folder paths and system folders such as /etc
- placeholders like path/to/file.py, your-project/ and my_script.py
- lines that call a path an example, say it gets created or generated, say
  it was renamed, tell the agent not to use it, or put it upstream or in
  another repo ("in the foo repo")
- lines that explain glob patterns, where the paths are made up
- paths into build output folders (dist, build, node_modules, .venv and the
  like) and anything your .gitignore covers
- commands for tools it does not model, and `python -m` for third-party
  modules

Path checks follow the folder a code block has `cd`'d into, and they resolve
relative links from the file that contains them. Existence checks are
case-exact, even on Windows and macOS, so `makefile` does not match
`Makefile`. Env var checks count any other mention in the repo, including
`.env.example` and other templates, and never read your real `.env`.

## Use it as a pre-commit hook

```yaml
repos:
  - repo: https://github.com/amagilabs/amagi-staleref
    rev: v0.1.0
    hooks:
      - id: amagi-staleref
```

The hook scans the whole repo on every commit, because renaming a source
file can break a doc you never touched.

## Use it in CI

GitHub Actions runners ship with pipx. `--format github` turns each finding
into an annotation on the pull request:

```yaml
- uses: actions/checkout@v4
- run: pipx install amagi-staleref
- run: amagi-staleref --format github
```

Lines look like this:

```text
::error file=.cursorrules,line=1,title=SR001 path-missing::'src/components/' does not exist
```

Use `--fail-on error` to let warnings through, or `--format json` for
anything else that reads results.

## Use it as a Claude Code skill

The repo includes a `SKILL.md`. Copy it into your skills folder:

```
mkdir -p ~/.claude/skills/amagi-staleref
cp SKILL.md ~/.claude/skills/amagi-staleref/SKILL.md
```

Then ask Claude to check your CLAUDE.md for stale references, or to find the
docs that still point at a file you just moved. It runs `amagi-staleref` and
fixes what it finds.

## Limitations

- It only checks what it can resolve inside the repo. A path into another
  repo, a package's own files or your home folder is skipped.
- A line that calls a path an example gets skipped as a whole, so a real
  stale path on that same line is missed.
- The env var check is a word search. A name that appears anywhere else in
  the repo, even in a comment, counts as used.
- Script checks read package.json, Makefile, justfile and Taskfile.yml.
  Turborepo and nx task graphs, Gradle tasks and Cargo aliases are not
  checked.
- Bare file names in prose (SR002) are off by default, because docs often
  name files that belong to other tools.

## Development

Clone the repo and install it in editable mode with the `dev` extra, which
brings in pytest and ruff:

```sh
git clone https://github.com/amagilabs/amagi-staleref.git
cd amagi-staleref
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Run the tests:

```sh
pytest -q
```

Run the linter. Its settings are under `[tool.ruff]` in `pyproject.toml`:

```sh
ruff check .
```

CI runs `pytest -q`, `ruff check .` and `amagi-staleref --format github` on
Python 3.9, 3.12 and 3.13, so run all three before you push. The last one
scans this repo's own README.

### The pre-commit hook

`.pre-commit-hooks.yaml` is what lets other repos use amagi-staleref as a
hook (see [Use it as a pre-commit hook](#use-it-as-a-pre-commit-hook)). When a
project's `.pre-commit-config.yaml` points at this repo, pre-commit clones it
at the given `rev`, installs the package into its own virtualenv
(`language: python`) and runs the `amagi-staleref` command. With
`pass_filenames: false` and `always_run: true` it scans the whole repo on
every commit, even when no Markdown file is staged.

To try a change to the hook before you tag a release, run it from another
git repo against your local clone:

```sh
pip install pre-commit
pre-commit try-repo path/to/amagi-staleref amagi-staleref --all-files
```

Users pin the hook with `rev`, so a change reaches them only after you push a
new tag and they run `pre-commit autoupdate`.

## Related AmagiLabs projects

- [amagi-skill-lint](https://github.com/amagilabs/amagi-skill-lint): checks
  SKILL.md and subagent files for broken frontmatter, weak descriptions and
  missing bundled files. amagi-staleref checks the instruction files around
  them.
- [amagi-watch](https://github.com/amagilabs/amagi-watch): give Claude a
  video URL and get back a transcript and key frames.
- [amagi-legal-review-skills](https://github.com/amagilabs/amagi-legal-review-skills):
  Claude Code skills and subagents for a first-pass review of contracts and
  leases.
- More at [github.com/amagilabs](https://github.com/amagilabs).

If amagi-staleref caught a doc that was sending your agent the wrong way,
star the repo so other people can find it.

## License

MIT. Copyright (c) 2026 Amagisolutionsgroup, Inc. (AmagiLabs). See [LICENSE](LICENSE).
