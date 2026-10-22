"""Find the agent instruction files and READMEs in a repo."""

import os
import posixpath

from .repo import glob_to_regex

INSTRUCTION_NAMES = {
    "claude.md", "claude.local.md", "agents.md", "agent.md", "gemini.md",
    ".cursorrules", ".windsurfrules", ".clinerules", "copilot-instructions.md",
}
README_NAMES = {"readme.md", "readme.markdown", "readme.mdx", "readme", "readme.txt"}
RULE_DIRS = (".cursor/rules/", ".clinerules/", ".windsurf/rules/", ".github/instructions/", ".roo/rules/")
# Files that are often gitignored but still read by the agent.
LOCAL_ONLY = ("CLAUDE.local.md", ".cursorrules", ".windsurfrules", ".clinerules")


def is_instruction(rel, readme=True):
    base = posixpath.basename(rel).lower()
    if base in INSTRUCTION_NAMES:
        return True
    if readme and base in README_NAMES:
        return True
    if any(rel.startswith(d) or ("/" + d) in rel for d in RULE_DIRS):
        return base.endswith((".md", ".mdc", ".txt"))
    return False


def find_instruction_files(repo, readme=True, include=(), exclude=()):
    inc = [glob_to_regex(p.strip("/")) for p in include]
    exc = [glob_to_regex(p.strip("/")) for p in exclude]
    found = set()
    for rel in repo.files:
        if is_instruction(rel, readme) or any(rx.match(rel) for rx in inc):
            found.add(rel)
    for name in LOCAL_ONLY:
        if name not in found and os.path.isfile(os.path.join(repo.root, name)):
            found.add(name)
    return sorted((f for f in found if not any(rx.match(f) for rx in exc)), key=_order)


def _order(rel):
    """Root files first, agent files before READMEs, then alphabetical."""
    base = posixpath.basename(rel).lower()
    return (rel.count("/"), base in README_NAMES, rel.lower())
