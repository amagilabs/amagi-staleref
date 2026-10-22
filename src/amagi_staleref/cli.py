"""amagi-staleref command line."""

import argparse
import os
import sys

from . import __version__, report
from .checks import FileChecker
from .discover import find_instruction_files
from .repo import Repo, _load_toml
from .rules import OPTIONAL, RULES, SEVERITY_ORDER


def load_config(root):
    """[tool.amagi-staleref] in pyproject.toml, or top-level keys in .amagi-staleref.toml."""
    path = os.path.join(root, ".amagi-staleref.toml")
    if os.path.isfile(path):
        return _load_toml(path)
    data = _load_toml(os.path.join(root, "pyproject.toml"))
    cfg = (data.get("tool") or {}).get("amagi-staleref") if isinstance(data.get("tool"), dict) else None
    return cfg if isinstance(cfg, dict) else {}


def _ids(value):
    if not value:
        return set()
    if isinstance(value, str):
        value = value.split(",")
    return {v.strip().upper() for v in value if v.strip()}


def scan(root, files=None, readme=True, include=(), exclude=(), ignore=(), select=(), enable=()):
    """Scan one repo root. Returns a result dict for the reporters."""
    repo = Repo(root)
    cfg = load_config(repo.root)
    include = list(include) + list(cfg.get("include", []))
    exclude = list(exclude) + list(cfg.get("exclude", []))
    ignore = _ids(ignore) | _ids(cfg.get("ignore"))
    select = _ids(select) | _ids(cfg.get("select"))
    enable = _ids(enable) | _ids(cfg.get("enable")) | select
    if cfg.get("readme") is False:
        readme = False
    all_instr = find_instruction_files(repo, readme=readme, include=include, exclude=exclude)
    targets = files if files is not None else all_instr
    findings, checked = [], 0
    every = set(all_instr) | set(targets)
    checkers = []
    for rel in targets:
        checker = FileChecker(repo, rel, instruction_files=every)
        checker.run(repo.read_text(rel), finish=False)
        checkers.append(checker)
    repo.prefetch_env({n for c in checkers for _, n in c.env_refs})
    for checker in checkers:
        for f in checker.finish():
            if f.rule in ignore or (select and f.rule not in select) or (f.rule in OPTIONAL and f.rule not in enable):
                continue
            findings.append(f)
        checked += checker.checked
    return {"files": list(targets), "findings": findings, "checked": checked,
            "roots_without_files": [] if targets else [root]}


def build_parser():
    p = argparse.ArgumentParser(
        prog="amagi-staleref",
        description="Find stale paths, scripts, commands and env vars in CLAUDE.md, AGENTS.md, "
                    ".cursorrules and README files.",
    )
    p.add_argument("paths", nargs="*", metavar="PATH",
                   help="repo folder to scan (default: current folder), or specific files to check")
    p.add_argument("--root", help="repo root used to resolve references when PATH is a file (default: current folder)")
    p.add_argument("--format", choices=("text", "json", "github"), default="text")
    p.add_argument("--fail-on", choices=("error", "warning", "info", "never"), default=None,
                   help="lowest severity that makes the exit code 1 (default: warning)")
    p.add_argument("--no-readme", action="store_true", help="skip README files, check agent files only")
    p.add_argument("--include", action="append", default=[], metavar="GLOB",
                   help="also check files matching GLOB (repeatable), e.g. 'docs/**/*.md'")
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB",
                   help="skip files matching GLOB (repeatable)")
    p.add_argument("--ignore", default="", metavar="IDS", help="rules to skip, e.g. SR040,SR002")
    p.add_argument("--select", default="", metavar="IDS", help="run only these rules")
    p.add_argument("--enable", default="", metavar="IDS", help="turn on rules that are off by default (SR002)")
    p.add_argument("--quiet", action="store_true", help="print nothing when nothing is stale")
    p.add_argument("--list-rules", action="store_true", help="print every rule and exit")
    p.add_argument("--version", action="version", version=f"amagi-staleref {__version__}")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.list_rules:
        print(report.rules_table())
        return 0
    for rid in _ids(args.ignore) | _ids(args.select) | _ids(args.enable):
        if rid not in RULES:
            print(f"amagi-staleref: unknown rule '{rid}' (see --list-rules)", file=sys.stderr)
            return 2

    paths = args.paths or ["."]
    dirs = [p for p in paths if os.path.isdir(p)]
    files = [p for p in paths if os.path.isfile(p)]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        print(f"amagi-staleref: no such file or folder: {', '.join(missing)}", file=sys.stderr)
        return 2

    results = []
    common = dict(readme=not args.no_readme, include=args.include, exclude=args.exclude,
                  ignore=args.ignore, select=args.select, enable=args.enable)
    for d in dirs:
        results.append(scan(d, **common))
    if files:
        root = os.path.abspath(args.root or ".")
        rels = []
        for f in files:
            rel = os.path.relpath(os.path.abspath(f), root).replace("\\", "/")
            if rel.startswith(".."):
                print(f"amagi-staleref: {f} is outside the root {root}; pass --root", file=sys.stderr)
                return 2
            rels.append(rel)
        results.append(scan(root, files=rels, **common))

    merged = {"files": [], "findings": [], "checked": 0, "roots_without_files": []}
    for r, d in zip(results, dirs + (["."] if files else [])):
        prefix = "" if len(dirs) + bool(files) == 1 or d == "." else d.rstrip("/\\").replace("\\", "/") + "/"
        merged["files"] += [prefix + f for f in r["files"]]
        for f in r["findings"]:
            f.file = prefix + f.file
        merged["findings"] += r["findings"]
        merged["checked"] += r["checked"]
        merged["roots_without_files"] += r["roots_without_files"]

    if not merged["files"]:
        if args.format == "text":
            print(report.text(merged))
        elif args.format == "json":
            print(report.as_json(merged))
        return 2 if not args.quiet else 0

    if args.format == "json":
        print(report.as_json(merged))
    elif args.format == "github":
        print(report.github(merged))
    elif not (args.quiet and not merged["findings"]):
        print(report.text(merged, quiet=args.quiet))

    cfg_fail = None
    if len(dirs) == 1 and not files:
        cfg_fail = load_config(os.path.abspath(dirs[0])).get("fail-on")
    fail_on = args.fail_on or cfg_fail or "warning"
    if fail_on == "never":
        return 0
    floor = SEVERITY_ORDER[fail_on]
    return 1 if any(SEVERITY_ORDER[f.severity] >= floor for f in merged["findings"]) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
