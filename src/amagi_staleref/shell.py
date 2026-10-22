"""Turn code blocks and code spans into simple commands.

This is not a shell. It splits text into argv lists on && || ; | and &,
pulls off leading VAR=value assignments, drops redirect targets, strips
prompts like "$ " and "PS>", joins continued lines and skips heredoc bodies.
"""

import re
import shlex
from dataclasses import dataclass, field

SHELL_LANGS = {
    "", "sh", "bash", "shell", "zsh", "fish", "console", "terminal", "shell-session",
    "shellsession", "powershell", "ps1", "pwsh", "ps", "cmd", "bat", "batch", "bash-session",
}
POWERSHELL_LANGS = {"powershell", "ps1", "pwsh", "ps", "cmd", "bat", "batch"}
SESSION_LANGS = {"console", "terminal", "shell-session", "shellsession", "bash-session"}

PROMPT_RE = re.compile(r"^\s*(?:\$|%|❯|λ|PS(?: [^>]*)?>)\s+")
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
TREE_CHARS = set("├└│─┬┼┐┘┌")
OPERATORS = {"&&", "||", ";", "|", "&", "|&", ";;", "(", ")", "{", "}"}
REDIRECTS = {">", ">>", "<", "<<", "<<<", ">&", "&>", "&>>", ">|", "<>", "2>", "2>>"}


@dataclass
class Command:
    line: int
    argv: list
    assigns: list = field(default_factory=list)  # [(name, value)]
    raw: str = ""


def _split_ops(text):
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|<>()")
    lex.whitespace_split = True
    lex.commenters = "#"
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes: fall back to whitespace split
        return text.split()


def split_commands(text, line=0):
    """Split one logical command line into Command objects."""
    tokens = _split_ops(text)
    out = []
    cur = []
    skip_next = False
    for tok in tokens:
        if skip_next:
            skip_next = False
            continue
        if tok in OPERATORS or (tok and set(tok) <= set(";&|()")):
            if cur:
                out.append(cur)
            cur = []
            continue
        if tok in REDIRECTS or (tok and set(tok) <= set("<>&") and tok not in ("&",)):
            skip_next = True
            if cur and cur[-1].isdigit():  # the "2" in 2>/dev/null
                cur.pop()
            continue
        cur.append(tok)
    if cur:
        out.append(cur)

    cmds = []
    for argv in out:
        assigns = []
        while argv and ASSIGN_RE.match(argv[0]):
            name, _, value = argv[0].partition("=")
            assigns.append((name, value))
            argv = argv[1:]
        # sudo / env / time / exec wrappers don't change what runs
        while argv and argv[0] in ("sudo", "time", "exec", "nohup", "command", "env"):
            argv = argv[1:]
            while argv and ASSIGN_RE.match(argv[0]):
                name, _, value = argv[0].partition("=")
                assigns.append((name, value))
                argv = argv[1:]
        if argv or assigns:
            cmds.append(Command(line=line, argv=argv, assigns=assigns, raw=text))
    return cmds


def block_commands(lines, lang=""):
    """Commands from a fenced block given as [(line_no, text)]. Returns [] for non-shell blocks."""
    lang = (lang or "").lower()
    if lang not in SHELL_LANGS:
        return []
    powershell = lang in POWERSHELL_LANGS
    session = lang in SESSION_LANGS or any(PROMPT_RE.match(t) for _, t in lines)

    logical = []  # (line_no, text)
    buf = None
    heredoc = None
    for no, text in lines:
        if heredoc is not None:
            if text.strip() == heredoc:
                heredoc = None
            continue
        if any(c in TREE_CHARS for c in text):
            continue
        if buf is None:
            m = PROMPT_RE.match(text)
            if session:
                if not m:
                    continue  # output line in a terminal transcript
                text = text[m.end():]
            elif m:
                text = text[m.end():]
            buf = (no, text)
        else:
            buf = (buf[0], buf[1] + " " + text.strip())
        body = buf[1].rstrip()
        cont = "`" if powershell else "\\"
        if body.endswith(cont) or (lang in ("cmd", "bat", "batch") and body.endswith("^")):
            buf = (buf[0], body[:-1])
            continue
        hd = HEREDOC_RE.search(body)
        if hd:
            heredoc = hd.group(2)
        logical.append(buf)
        buf = None
    if buf is not None:
        logical.append(buf)

    cmds = []
    for no, text in logical:
        if powershell:
            text = text.replace("\\", "/")
            text = re.sub(r"\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*", r"\1=", text)
        cmds.extend(split_commands(text, no))
    return cmds
