"""Text, JSON and GitHub Actions output."""

import json

from .rules import RULES, slug_of


def _counts(findings):
    counts = {"error": 0, "warning": 0, "info": 0}
    for f in findings:
        counts[f.severity] += 1
    return counts


def _plural(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def text(result, quiet=False):
    lines = []
    by_file = {}
    for f in result["findings"]:
        by_file.setdefault(f.file, []).append(f)
    for path in result["files"]:
        items = by_file.get(path)
        if not items:
            continue
        lines.append(path)
        for f in items:
            lines.append(f"  {f.line:>4}  {f.severity:<7}  {f.rule}  {f.message}")
        lines.append("")
    n_files = len(result["files"])
    c = _counts(result["findings"])
    total = len(result["findings"])
    head = f"Checked {_plural(result['checked'], 'reference')} in {_plural(n_files, 'file')}."
    if total == 0:
        lines.append(head + " Nothing stale.")
    else:
        parts = [_plural(c[k], k) for k in ("error", "warning", "info") if c[k]]
        lines.append(f"{head} {total} stale: " + ", ".join(parts) + ".")
    if not quiet and result.get("roots_without_files"):
        for root in result["roots_without_files"]:
            lines.append(f"(no CLAUDE.md, AGENTS.md, .cursorrules or README found under {root})")
    return "\n".join(lines)


def as_json(result):
    return json.dumps({
        "files": result["files"],
        "checked": result["checked"],
        "summary": _counts(result["findings"]),
        "findings": [
            {
                "file": f.file, "line": f.line, "rule": f.rule, "slug": slug_of(f.rule),
                "severity": f.severity, "ref": f.ref, "message": f.message,
            }
            for f in result["findings"]
        ],
    }, indent=2)


def _gh_escape(s):
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def github(result):
    out = []
    for f in result["findings"]:
        level = {"error": "error", "warning": "warning", "info": "notice"}[f.severity]
        out.append(
            f"::{level} file={f.file},line={f.line},title={f.rule} {slug_of(f.rule)}"
            f"::{_gh_escape(f.message)}"
        )
    c = _counts(result["findings"])
    out.append(f"amagi-staleref: {result['checked']} references in {len(result['files'])} files, "
               f"{c['error']} errors, {c['warning']} warnings")
    return "\n".join(out)


def rules_table():
    lines = []
    for rid, (slug, sev, desc) in RULES.items():
        lines.append(f"{rid}  {sev:<7}  {slug:<24}  {desc}")
    return "\n".join(lines)
