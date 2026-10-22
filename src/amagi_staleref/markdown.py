"""Pull the checkable pieces out of an instruction file.

Only text a reader would treat as literal is used: inline code spans, fenced
code blocks, relative Markdown links and CLAUDE.md @-imports. Ordinary prose
is never parsed for paths or commands, which keeps false positives down.
"""

import re
from dataclasses import dataclass, field

FENCE_RE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})\s*([^\s`]*)")
LINK_RE = re.compile(r"!?\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)>?(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
EXTERNAL_AFTER_RE = re.compile(r"[^\[\]]*\]\(\s*<?[A-Za-z][A-Za-z0-9+.-]*:")
REF_LINK_RE = re.compile(r"^\s{0,3}\[(?!\^)[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$")  # not [^footnote]:
IMPORT_RE = re.compile(r"(?:^|(?<=[\s(\[]))@((?:\.{1,2}/|~/|/)?[A-Za-z0-9_][A-Za-z0-9_.\-/]*)")
DISABLE_RE = re.compile(r"staleref-disable\s+([A-Z0-9 ,]+)")
IGNORE_MARK = "staleref-ignore"
HTML_COMMENT_RE = re.compile(r"<!--.*?-->")


@dataclass
class Span:
    line: int
    text: str


@dataclass
class Block:
    line: int  # line of the opening fence
    lang: str
    lines: list = field(default_factory=list)  # [(line_no, text)]


@dataclass
class Extracted:
    spans: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    links: list = field(default_factory=list)    # [Span]
    imports: list = field(default_factory=list)  # [Span]
    ignored_lines: set = field(default_factory=set)
    disabled: set = field(default_factory=set)


def _code_spans(line):
    """Yield (start, end, content) for each inline code span in one line."""
    i = 0
    n = len(line)
    while i < n:
        if line[i] != "`":
            i += 1
            continue
        j = i
        while j < n and line[j] == "`":
            j += 1
        ticks = line[i:j]
        close = line.find(ticks, j)
        # the closing run must be exactly as long as the opening run
        while close != -1 and close + len(ticks) < n and line[close + len(ticks)] == "`":
            close = line.find(ticks, close + len(ticks) + 1)
        if close == -1:
            i = j
            continue
        content = line[j:close]
        if len(ticks) > 1 and content.startswith(" ") and content.endswith(" ") and content.strip():
            content = content[1:-1]
        yield i, close + len(ticks), content
        i = close + len(ticks)


def _external_link_text(line, s, e):
    """True for [`numpy/_globals.py`](https://...): the code names a file in another repo."""
    before = line[:s]
    return before.rfind("[") > before.rfind("]") and bool(EXTERNAL_AFTER_RE.match(line, e))


def _frontmatter_end(lines):
    if not lines or lines[0].strip() != "---":
        return 0
    for idx in range(1, min(len(lines), 200)):
        if lines[idx].strip() in ("---", "..."):
            return idx + 1
    return 0


def extract(text, imports=False):
    """Parse one file. `imports` turns on CLAUDE.md-style @path imports."""
    out = Extracted()
    lines = text.splitlines()
    start = _frontmatter_end(lines)
    block = None
    fence = None
    ignore_next = False
    for idx in range(start, len(lines)):
        raw = lines[idx]
        no = idx + 1
        if block is not None:
            m = FENCE_RE.match(raw)
            if m and m.group(2)[0] == fence[0] and len(m.group(2)) >= len(fence) and not m.group(3):
                out.blocks.append(block)
                block = None
                continue
            block.lines.append((no, raw))
            continue

        for m in DISABLE_RE.finditer(raw):
            out.disabled.update(t for t in re.split(r"[\s,]+", m.group(1)) if t)

        if ignore_next and raw.strip():
            out.ignored_lines.add(no)
            ignore_next = False
        if IGNORE_MARK in raw:
            out.ignored_lines.add(no)
            if not HTML_COMMENT_RE.sub("", raw).replace(IGNORE_MARK, "").strip(" #/-<>!"):
                ignore_next = True

        m = FENCE_RE.match(raw)
        if m:
            fence = m.group(2)
            block = Block(line=no, lang=m.group(3).lower())
            if no in out.ignored_lines:
                block.lang = "__ignored__"
            continue

        prose = raw
        spans = list(_code_spans(raw))
        for s, e, content in spans:
            if content.strip() and not _external_link_text(raw, s, e):
                out.spans.append(Span(no, content.strip()))
        for s, e, _ in reversed(spans):
            prose = prose[:s] + " " * (e - s) + prose[e:]
        prose = HTML_COMMENT_RE.sub(lambda mm: " " * len(mm.group(0)), prose)

        for lm in LINK_RE.finditer(prose):
            out.links.append(Span(no, lm.group(1)))
        rm = REF_LINK_RE.match(prose)
        if rm:
            out.links.append(Span(no, rm.group(1)))
        if imports:
            for im in IMPORT_RE.finditer(prose):
                out.imports.append(Span(no, im.group(1).rstrip(".,;:!?)]")))
    if block is not None:  # unclosed fence runs to end of file
        out.blocks.append(block)
    out.blocks = [b for b in out.blocks if b.lang != "__ignored__"]
    return out
