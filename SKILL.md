---
name: amagi-staleref
description: The amagi-staleref CLI checks CLAUDE.md, AGENTS.md, .cursorrules, other agent rule files and READMEs for references that no longer match the repo - file paths and globs that point at nothing, npm/pnpm/yarn/bun scripts, make targets, just recipes and Taskfile tasks that are not defined, python -m modules that are gone, cd into missing folders, and env vars nothing else mentions. Use when the user wants to audit or clean up agent instruction files, asks why an agent keeps running a command that does not exist, has just renamed or moved files and wants to find docs that still point at the old names, or wants a pre-commit hook or CI step that catches stale docs.
---

# amagi-staleref

## When to use

- An agent keeps trying a script, path or `make` target that is not there any more.
- Files were moved or renamed and the user wants every doc that still points at the
  old location.
- The user wants CLAUDE.md, AGENTS.md or a README checked before a commit, or a CI
  step that fails when they drift.

It reads files only. No network, no LLM calls, no API keys. It does not lint
SKILL.md files or agent frontmatter; that is amagi-skill-lint's job.

## How to invoke

```
amagi-staleref [PATH ...] [--root DIR] [--format text|json|github]
               [--fail-on error|warning|info|never] [--no-readme]
               [--include GLOB] [--exclude GLOB] [--ignore IDS] [--select IDS]
               [--enable IDS] [--quiet] [--list-rules]
```

PATH is a repo folder (default: the current folder) or one or more files. When you
pass files, references are resolved against `--root` (default: the current folder).

Examples:

```
amagi-staleref
amagi-staleref --no-readme
amagi-staleref CLAUDE.md AGENTS.md
amagi-staleref . --format github --fail-on error
amagi-staleref --list-rules
```

## What to read afterward

- One block per file with problems: line number, severity, rule ID, message. Path
  findings often end with a hint such as "a file with that name is at
  src/app/api/routes.py" or "did you mean 'lint'?". Use the hint to fix the doc.
- The summary line: references checked, files read, and the error/warning counts.
- Exit codes: 0 nothing at or above `--fail-on` (default warning), 1 stale
  references found, 2 usage error or no instruction files found.

## Fixing common findings

- SR001 path-missing: update the path in the doc, or delete the line if the file
  is gone for good.
- SR010 script-missing / SR020 make-target-missing / SR022 just-recipe-missing:
  the doc names a command the repo no longer defines. Use the name the hint
  suggests, or add the script back.
- SR030 python-module-missing: the module was moved or renamed. Point at the new
  dotted path.
- SR040 env-var-unused: nothing in the code, config or `.env.example` uses the
  variable. Remove it from the doc, or add it to `.env.example` if it is real.
- SR050 cd-missing: the folder was renamed. The rest of that code block is
  skipped, so fix this first and re-run.
- A line that is meant to name a path that does not exist (an example, a file
  the user creates later): end it with `<!-- staleref-ignore -->`. To turn rules
  off for a whole file: `<!-- staleref-disable SR040 -->`.

## Config

`[tool.amagi-staleref]` in `pyproject.toml`, or the same keys at the top level of
`.amagi-staleref.toml`: `include`, `exclude`, `ignore`, `select`, `enable`,
`readme` (true/false) and `fail-on`.
