"""Check every reference in one instruction file against the repo."""

import difflib
import posixpath
import re
from dataclasses import dataclass
from urllib.parse import unquote

from . import markdown, shell
from .repo import canon, join, norm
from .rules import severity_of

# ------------------------------------------------------------------ path shape
KNOWN_EXTS = set("""
py pyi pyx ipynb js mjs cjs jsx ts mts cts tsx json jsonc json5 yaml yml toml ini cfg conf md mdx
mdc rst txt adoc sh bash zsh fish ps1 psm1 bat cmd go mod rs rb erb java kt kts scala swift m mm c h
cc cpp cxx hpp hh cs csproj sln fs vb php pl lua r jl dart ex exs erl hs ml clj sql prisma graphql
gql proto html htm css scss sass less vue svelte astro xml svg csv tsv lock env example sample
template tmpl tpl j2 jinja hbs ejs pug tf tfvars hcl nix dockerfile gradle properties plist wasm zig
sol mk just png jpg jpeg gif ico webp pdf xlsx docx pem crt patch diff http rego cue bicep
""".split())
KNOWN_NAMES = {
    "makefile", "gnumakefile", "dockerfile", "containerfile", "justfile", "procfile", "gemfile",
    "rakefile", "vagrantfile", "jenkinsfile", "brewfile", "pipfile", "license", "codeowners",
    "caddyfile", "taskfile", "earthfile", "tiltfile", "podfile", "cartfile", "fastfile",
}
TECH_NAMES = {
    "node.js", "next.js", "vue.js", "nuxt.js", "express.js", "three.js", "chart.js", "d3.js",
    "react.js", "angular.js", "ember.js", "backbone.js", "alpine.js", "solid.js", "nest.js",
    "p5.js", "pixi.js", "anime.js", "video.js", "moment.js", "day.js", "math.js", "ml5.js",
    "htmx.js", "socket.io", "deno.land", "bun.sh", "vite.js", "ethers.js", "web3.js",
}
PLACEHOLDER_SEGMENTS = {
    "foo", "bar", "baz", "qux", "xxx", "xx", "yyy", "zzz", "path", "to", "example", "examples-here",
    "your", "yours", "mine", "something", "somewhere", "whatever", "name", "file", "dir", "folder",
}
PLACEHOLDER_WORDS = {
    "foo", "bar", "baz", "qux", "xxx", "nnn", "yyyy", "todo", "tbd", "specific", "whatever",
}
PLACEHOLDER_LEADS = {"example", "sample", "my", "your", "some", "new", "another", "other", "hello"}
# Install-layout folders (lib/gstreamer-1.0/, usr/local/bin): about a system, not this repo,
# unless the repo really has a folder by that name.
SYSTEM_DIRS = {"lib", "lib64", "bin", "sbin", "usr", "etc", "var", "opt", "share", "include", "tmp",
               "home", "Library", "Applications", "Contents", "System"}
# Dotted names that are code, not files: process.env, import.meta.env, os.path ...
CODE_PREFIX_RE = re.compile(
    r"^(?:process|import\.meta|Deno|Bun|window|document|globalThis|module|exports|console)\.")
# Bare dotfiles worth checking. Anything else that starts with a dot (".sha256", ".all",
# ".test.ts") is a suffix, a method or an extension, not a file.
DOTFILE_RE = re.compile(
    r"^\.(?:env|envrc|git\w*|docker\w*|npmrc|nvmrc|yarnrc|pnpm\w*|eslint\w*|prettier\w*|stylelint\w*|babel\w*|"
    r"editorconfig|browserslistrc|python-version|node-version|ruby-version|tool-versions|terraform\w*|"
    r"pre-commit[\w-]*|cursor\w*|windsurf\w*|clinerules|claude|codex|vscode|devcontainer|husky|changeset|"
    r"storybook|circleci|gitlab-ci|travis|mcp|swcrc|lintstagedrc|commitlintrc|markdownlint\w*|yamllint|"
    r"flake8|pylintrc|coveragerc|pypirc|isort|ruff|mypy|vercel\w*|netlify\w*|firebase\w*|roo\w*|aider\w*|"
    r"amagi-staleref|justfile|dev\.vars|gemini)(?:\.[\w.-]+)?$")
BAD_CHARS = set("<>{}$%|^`'\"()*?[],;=!") - set("*?[")

_LINE_SUFFIX = re.compile(r"(?::\d+(?:[-:]\d+)?|#L\d+(?:-L?\d+)?)$")


def clean_token(tok):
    tok = tok.strip().strip("'\"")
    tok = re.sub(r"::.*$", "", tok)  # pytest node ids: tests/test_x.py::test_y
    tok = _LINE_SUFFIX.sub("", tok)
    m = re.match(r"^([^#]+\.[A-Za-z0-9]+)#[\w-]+$", tok)  # docs/guide.md#some-heading
    if m:
        tok = m.group(1)
    tok = tok.rstrip(".,;:!?)")
    if tok.startswith(".\\"):
        tok = tok.replace("\\", "/")
    return tok


def is_placeholder(tok):
    """path/to/file, your-app/, adr_NNN_title.md, Foo.test.tsx, new-project ..."""
    low = tok.lower()
    lowered = [s for s in low.strip("/").split("/") if s not in ("", ".", "..")]
    if not lowered:
        return False
    if "path/to" in low or any(s in PLACEHOLDER_SEGMENTS for s in lowered[:-1]):
        return True
    if any(s.startswith(("your-", "your_", "my-", "my_", "my.")) for s in lowered):
        return True
    if any(re.fullmatch(r"[x]{3,}|y{4}|[a-z]+-?n+|n{3,}", s) for s in lowered):
        return True
    parts = [p for p in re.split(r"[._-]", lowered[-1]) if p]
    if any(p in PLACEHOLDER_WORDS for p in parts):
        return True
    return (bool(parts) and not lowered[-1].startswith(".") and parts[0] in PLACEHOLDER_LEADS
            and "/" not in tok.strip("/"))


def classify(tok):
    """Return 'path', 'maybe', 'bare', 'glob' or None for a single token."""
    if not tok or len(tok) > 300 or any(c.isspace() for c in tok):
        return None
    low = tok.lower()
    if "://" in tok or low.startswith(("mailto:", "data:", "git@", "www.")):
        return None
    if tok[0] in "-~/@#+&\\" or "\\" in tok or ":" in tok:
        return None
    if "..." in tok or "…" in tok or tok in (".", ".."):
        return None
    if low in TECH_NAMES or CODE_PREFIX_RE.match(tok):
        return None
    if not any(c.isalnum() for c in tok):
        return None
    glob = any(c in tok for c in "*?[")
    if glob and "{" in tok:
        if any(c in tok for c in "<>$%|^`'\"(),;=!") or tok.count("{") != tok.count("}"):
            return None
    elif any(c in BAD_CHARS for c in tok):
        return None
    segments = [s for s in tok.strip("/").split("/") if s not in (".", "..")]
    if not segments or is_placeholder(tok):
        return None
    lowered = [s.lower() for s in segments]
    if BARE_ENV_RE.match(segments[0]) or segments[0] in KNOWN_ENV:
        return None  # CODEX_HOME/config.toml: a folder named by an env var
    if glob:
        # A glob is a file pattern when it names an extension (*.tsx, src/**/test_*.py) or ends
        # in a folder wildcard (src/*, docs/**). Test*, getBy*, *_KEY and */list are identifiers.
        last = lowered[-1]
        ext = last.rsplit(".", 1)[1] if "." in last else ""
        if ext not in KNOWN_EXTS and not ("/" in tok.rstrip("/") and last in ("*", "**")):
            return None
        return "glob"
    name = lowered[-1]
    ext = name.rsplit(".", 1)[1] if "." in name.lstrip(".") else ""
    has_ext = ext in KNOWN_EXTS or name in KNOWN_NAMES
    if tok.startswith(("./", "../")):
        return "path"
    if "/" in tok.rstrip("/"):
        if (not has_ext and re.search(r"[A-Z_]", segments[-1].rsplit(".", 1)[-1])
                and "." in segments[-1][1:]):
            return None  # mappers/__init__.PRESETS: a Python attribute, not a file
        return "path" if has_ext or tok.endswith("/") else "maybe"
    if tok.endswith("/"):
        return None  # a lone "lib/" or "prefix/" in prose is rarely a path in this repo
    if name.startswith("."):
        rest = name[1:]
        if rest in KNOWN_EXTS and rest != "env":
            return None  # ".md" means the extension, not a file
        return "bare" if DOTFILE_RE.match(name) else None
    return "bare" if has_ext else None


# A missing path on a line like this is usually one the reader is told to create,
# avoid or rename, or a made-up example. Those lines are not checked for paths.
HYPOTHETICAL_RE = re.compile(
    r"(?i)\b(?:never|don't|do not|must not|should not|shouldn't|avoid|instead of|"
    r"renam(?:e|es|ed|ing)|"
    r"creat(?:e|es|ed|ing)|new file|will be (?:created|generated|written)|generat(?:e|es|ed|ing)|"
    r"e\.g\.|for example|formerly|previously|no longer|used to be|(?:git)?ignored|scaffold|"
    r"upstream|"
    r"your (?:own )?(?:repo|repository|project)|(?:in|into|from) the [\w.-]+ repo(?:sitory)?\b|"
    r"port of|"
    r"(?:like|such as) `|output (?:goes |is written )?(?:to|in)|writes? (?:to|out)|"
    r"saved? (?:to|in|as))")
# Lines that explain glob or path matching use made-up paths: "`*.py` matches `src/main.py`".
PATTERN_TALK_RE = re.compile(
    r"(?i)\b(?:pattern|patterns|match(?:es|ed)?|denies|allowlist|denylist)\b")

# ------------------------------------------------------------------ env vars
ENV_PATTERNS = [
    re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"process\.env\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"),
    re.compile(r"import\.meta\.env\.([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"os\.environ\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"),
    re.compile(r"os\.(?:environ\.get|getenv)\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),
    re.compile(r"Deno\.env\.get\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),
    re.compile(r"env::var\(\s*\"([A-Za-z_][A-Za-z0-9_]*)\""),
    re.compile(r"\bENV\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"),
    re.compile(r"\$env:([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?:[:?\-=+][^}]*)?\}"),
    re.compile(r"\$([A-Z][A-Z0-9_]+)\b"),
    re.compile(r"%([A-Z][A-Z0-9_]+)%"),
]
BARE_ENV_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+$")
ASSIGN_SPAN_RE = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)=\S*$")
ENV_CONTEXT_RE = re.compile(
    r"(?i)\benv\b|\.env|environment|variable|\bexport\b|secret|token|api[ _-]?key|credential")

KNOWN_ENV = set("""
PATH HOME USER USERNAME USERPROFILE SHELL TERM LANG LANGUAGE TZ TMPDIR TEMP TMP PWD OLDPWD EDITOR
VISUAL PAGER HOSTNAME LOGNAME DISPLAY COLUMNS LINES NO_COLOR FORCE_COLOR CLICOLOR CI DEBUG PORT HOST
HTTP_PROXY HTTPS_PROXY NO_PROXY ALL_PROXY SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE
CURL_CA_BUNDLE
NODE_ENV NODE_OPTIONS NODE_PATH NODE_EXTRA_CA_CERTS NODE_TLS_REJECT_UNAUTHORIZED
PYTHONPATH PYTHONHOME PYTHONUNBUFFERED PYTHONDONTWRITEBYTECODE PYTHONIOENCODING PYTHONUTF8
PYTHONWARNINGS PYTHONBREAKPOINT PYTHONHASHSEED VIRTUAL_ENV CONDA_PREFIX CONDA_DEFAULT_ENV
JAVA_HOME ANDROID_HOME ANDROID_SDK_ROOT GOPATH GOROOT GOBIN GOFLAGS GOOS GOARCH GOPROXY GOPRIVATE
CGO_ENABLED GO111MODULE RUST_LOG RUST_BACKTRACE RUSTFLAGS KUBECONFIG DOCKER_HOST DOCKER_BUILDKIT
OPENAI_API_KEY OPENAI_BASE_URL OPENAI_ORG_ID ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_MODEL
GEMINI_API_KEY GOOGLE_API_KEY GOOGLE_APPLICATION_CREDENTIALS GOOGLE_CLOUD_PROJECT MISTRAL_API_KEY
GROQ_API_KEY OPENROUTER_API_KEY COHERE_API_KEY HF_TOKEN HF_HOME HUGGING_FACE_HUB_TOKEN
GITHUB_TOKEN GH_TOKEN GITLAB_TOKEN NPM_TOKEN NODE_AUTH_TOKEN PYPI_TOKEN TWINE_USERNAME
TWINE_PASSWORD
SENTRY_DSN SENTRY_AUTH_TOKEN DATABASE_URL REDIS_URL LOG_LEVEL
EDITOR SUDO_USER WSL_DISTRO_NAME MSYSTEM COMSPEC SYSTEMROOT APPDATA LOCALAPPDATA PROGRAMFILES
""".split())
KNOWN_ENV_PREFIXES = (
    "GITHUB_", "RUNNER_", "ACTIONS_", "AWS_", "AZURE_", "ARM_", "GOOGLE_", "CLOUDSDK_", "CLAUDE_",
    "CLAUDE_CODE_", "ANTHROPIC_", "OPENAI_", "CODEX_", "CURSOR_", "UV_", "PIP_", "POETRY_", "PDM_",
    "HATCH_", "PIPENV_", "NPM_CONFIG_", "npm_", "YARN_", "PNPM_", "BUN_", "DENO_", "CARGO_",
    "RUSTUP_",
    "XDG_", "LC_", "DOCKER_", "COMPOSE_", "BUILDKIT_", "VERCEL_", "NETLIFY_", "CF_", "HF_", "TF_",
    "PYTEST_", "COVERAGE_", "TOX_", "NEXT_TELEMETRY", "GIT_", "SSH_", "GPG_", "OTEL_", "MSYS",
    "PROCESSOR_", "CONDA_", "MAMBA_", "JUPYTER_", "PLAYWRIGHT_", "PUPPETEER_", "CYPRESS_",
    "LANGCHAIN_", "LANGSMITH_", "OLLAMA_", "HOMEBREW_",
)

# ------------------------------------------------------------- command tables
NPM_LIFECYCLE = {"test": "test", "t": "test", "tst": "test", "start": "start", "stop": "stop"}
PNPM_BUILTINS = set("""
add install i update up upgrade remove rm uninstall un link ln unlink import rebuild rb prune fetch
patch patch-commit patch-remove dlx exec create init publish pack outdated list ls ll why audit
store env setup server root bin config c deploy licenses doctor approve-builds ignored-builds
self-update help recursive multi m dedupe catalog licenses cat-file cat-index find-hash sbom version
""".split())
YARN_BUILTINS = set("""
add install remove upgrade upgrade-interactive up why workspace workspaces dlx init info link unlink
create global cache config exec bin audit outdated publish version login logout pack list licenses
import check autoclean generate-lock-entry help node owner policies tag team set plugin constraints
dedupe npm explain rebuild patch patch-commit stage unplug search
""".split())
BUN_BUILTINS = set("""
install i add a remove rm update outdated link unlink pm publish audit info patch patch-commit x
create init upgrade build test repl exec why
""".split())
NODE_BIN_PACKAGES = {
    "tsc": "typescript", "ng": "@angular/cli", "nuxi": "nuxt", "svelte-kit": "@sveltejs/kit",
    "vue-cli-service": "@vue/cli-service", "remix": "@remix-run/dev", "biome": "@biomejs/biome",
    "changeset": "@changesets/cli", "commitlint": "@commitlint/cli",
    "playwright": "@playwright/test", "sb": "storybook", "eslint_d": "eslint_d",
    "webpack": "webpack-cli", "react-native": "react-native",
    "wrangler": "wrangler", "cdk": "aws-cdk", "sls": "serverless", "tsserver": "typescript",
}
PY_BIN_PACKAGES = {
    "py.test": "pytest", "django-admin": "django", "sphinx-build": "sphinx",
    "sphinx-autobuild": "sphinx-autobuild",
    "jupyter": "jupyter", "jupyter-lab": "jupyterlab", "ipython": "ipython", "fastapi": "fastapi",
    "pyright": "pyright", "pre-commit": "pre-commit", "mkdocs": "mkdocs", "f2py": "numpy",
    "pip": "pip", "python": "python", "python3": "python", "coverage": "coverage", "dmypy": "mypy",
    "stubgen": "mypy", "flask": "flask", "celery": "celery", "alembic": "alembic",
    "huey_consumer": "huey",
    "ruff": "ruff", "pytest": "pytest", "mypy": "mypy",
}
PY_ALWAYS = {"python", "python3", "pip", "pip3"}
# "uv run cargo fuzz" runs whatever is on PATH; these are not Python entry points.
NOT_PYTHON_TOOLS = set("""
cargo rustc rustup maturin node npm npx pnpm yarn bun deno make just task git docker podman bash sh
zsh pwsh powershell cmake ninja go java gradle mvn dotnet gh kubectl helm terraform ruby gem bundle
""".split())
PYTHONS = re.compile(r"^(?:python(?:\d(?:\.\d+)?)?|py|python\.exe|pypy3?)$")
CREATE_WORDS = {
    "create", "new", "init", "startproject", "startapp", "clone", "generate", "scaffold",
}
GENERIC = set("""
node deno ts-node tsx bash sh zsh fish source . pwsh powershell cat less more head tail bat code
cursor vim nvim vi nano emacs subl open xdg-open start chmod pytest ruby perl php rscript julia lua
java javac
gcc g++ clang rustc eslint prettier jest vitest mypy ruff black flake8 pylint isort pyright
coverage alembic streamlit kubectl ansible-playbook psql sqlite3 jq diff wc grep rg find ls tree cp
mv ln
pre-commit shellcheck yamllint markdownlint hadolint terraform helm pwsh.exe dot pandoc
""".split())
NO_CREATE_CHECK = {"cp", "mv", "ln"}  # the last argument is a destination
PATH_FLAG_TOOLS = {"docker", "docker-compose", "pip", "pip3", "podman"}
PATH_FLAGS = {"-f", "--file", "--env-file", "-r", "--requirement", "-c", "--constraint"}
PYTHON_M_PATH_MODULES = {
    "pytest", "unittest", "mypy", "ruff", "black", "flake8", "pylint", "isort", "coverage",
    "doctest", "pip",
}


@dataclass
class Finding:
    file: str
    line: int
    rule: str
    message: str
    ref: str

    @property
    def severity(self):
        return severity_of(self.rule)


class _Stop(Exception):
    """Abandon the rest of a code block (the working folder is unknown)."""


class FileChecker:
    def __init__(self, repo, rel, instruction_files=()):
        self.repo = repo
        self.rel = rel
        self.doc_dir = posixpath.dirname(rel)
        self.instruction_files = set(instruction_files) or {rel}
        self.findings = []
        self.checked = 0
        self._seen = set()
        self.env_refs = []  # (line, name), resolved in finish() with one repo-wide search
        self._ex = None

    # ------------------------------------------------------------ plumbing
    def add(self, line, rule, message, ref):
        key = (line, rule, ref)
        if key not in self._seen:
            self._seen.add(key)
            self.findings.append(Finding(self.rel, line, rule, message, ref))

    def run(self, text, finish=True):
        """Check one file. With finish=False the caller batches env var lookups
        across files (repo.prefetch_env) and then calls finish() itself."""
        name = posixpath.basename(self.rel).lower()
        ex = markdown.extract(text, imports=name.startswith(("claude", "gemini")))
        for span in ex.spans:
            self.check_span(span.line, span.text)
        for block in ex.blocks:
            self.check_block(block)
        for link in ex.links:
            self.check_link(link.line, link.text)
        for imp in ex.imports:
            self.check_import(imp.line, imp.text)
        self._ex = ex
        return self.finish() if finish else self.findings

    def finish(self):
        ex = self._ex
        if self.env_refs:
            hits = self.repo.env_files({n for _, n in self.env_refs})
            seen = set()
            for line, name in self.env_refs:
                if name in seen or self._doc_local_env(name):
                    continue
                seen.add(name)
                if not hits.get(name, set()) - self.instruction_files:
                    self.add(line, "SR040",
                             f"env var {name} is not mentioned anywhere else in the repo", name)
            self.env_refs = []
        self.findings = [
            f for f in self.findings if f.line not in ex.ignored_lines and f.rule not in ex.disabled
        ]
        self.findings.sort(key=lambda f: (f.line, f.rule))
        return self.findings

    def _doc_local_env(self, name):
        """A shell variable the doc's own script sets or demands: ${APP:?set me}, APP=x ... $APP."""
        self._line_text(1)
        text = "\n".join(self._lines)
        n = re.escape(name)
        if re.search(r"\$\{" + n + r":?[?=-]", text):
            return True
        return bool(re.search(r"(?m)^\s*(?:export\s+)?" + n + r"=", text)) and \
            bool(re.search(r"\$\{?" + n + r"\b", text))

    # ---------------------------------------------------------- path checks
    def _bases(self, cwd):
        out = []
        for b in (cwd, self.doc_dir, ""):
            if b is not None and b not in out:
                out.append(b)
        return out

    def check_path(self, line, raw, cwd=None, kind=None, rule_path="SR001"):
        tok = clean_token(raw)
        kind = kind or classify(tok)
        if kind is None:
            return
        bases = self._bases(cwd if cwd is not None else self.doc_dir)
        target = tok[2:] if tok.startswith("./") else tok
        real = [s for s in target.split("/") if s not in ("", ".", "..")]
        if real and real[0] in SYSTEM_DIRS and len(real) > 1 and not self.repo.is_dir(real[0]) \
                and not any(self.repo.is_dir(join(b, real[0])) for b in bases):
            return  # ../../etc/passwd, lib/gstreamer-1.0/: a system path
        if kind == "maybe" and len(real) == 2 and (target in self.repo.remote_slugs()):
            return  # owner/repo
        if kind in ("glob", "path", "maybe") and not tok.startswith(("./", "../")):
            first = target.split("/")[0]
            nested = "/" in target.rstrip("/")
            needs_dir = nested and (
                (kind == "glob" and not any(c in first for c in "*?[{"))
                or (kind != "glob" and (first.startswith(".") or first in SYSTEM_DIRS)))
            if needs_dir and not any(self.repo.is_dir(join(b, first)) for b in bases):
                # payloads/*.json, .codex/config.toml: about a folder this repo does not have
                return
        if kind == "glob":
            self.checked += 1
            for b in bases:
                if self.repo.glob(target, b):
                    return
            if (HYPOTHETICAL_RE.search(self._sentence(line))
                    or PATTERN_TALK_RE.search(self._line_text(line))):
                return
            if self.repo.is_generated(target) or self.repo.is_ignored(target):
                return
            self.add(line, "SR003", f"glob '{tok}' matches no files", tok)
            return
        if kind == "maybe":
            first = target.split("/")[0]
            if not any(self.repo.is_dir(join(b, first)) for b in bases):
                return  # not a path in this repo (owner/repo, feature/branch, text/html, ...)
        if (HYPOTHETICAL_RE.search(self._sentence(line))
                or PATTERN_TALK_RE.search(self._line_text(line))):
            return
        self.checked += 1
        candidates = [join(b, target) for b in bases]
        candidates = [c for c in candidates if not c.startswith("..")]
        if not candidates:
            self.checked -= 1
            return  # outside the repo, e.g. ../sibling-repo/
        for c in candidates:
            if self.repo.exists(c):
                return
        for c in candidates:
            if self.repo.is_generated(c) or self.repo.is_ignored(c):
                return
        if kind == "bare":
            base = posixpath.basename(target.rstrip("/"))
            if self.repo.files_named(base) or self.repo._secret_env(base):
                return  # a real .env is never committed, so it is never "missing"
            self.add(line, "SR002",
                     f"no file named '{tok}' anywhere in the repo{self._hint(target)}", tok)
            return
        if not tok.startswith(("./", "../")) and self.repo.suffix_match(target):
            return
        self.add(line, rule_path, f"'{tok}' does not exist{self._hint(target, candidates)}", tok)

    def _hint(self, target, candidates=()):
        base = posixpath.basename(target.rstrip("/"))
        for c in candidates:  # a near-miss next door beats a same-name file across the repo
            parent = posixpath.dirname(c.rstrip("/"))
            if not self.repo.is_dir(parent):
                continue
            prefix = parent + "/" if parent else ""
            siblings = sorted({p[len(prefix):].split("/")[0]
                               for p in self.repo.files | self.repo.dirs
                               if p.startswith(prefix) and p != parent})
            close = difflib.get_close_matches(base, siblings, n=1, cutoff=0.75)
            if close:
                return f"; did you mean {prefix}{close[0]}?"
        same = sorted(self.repo.files_named(base), key=len)
        if same:
            return f"; a file with that name is at {same[0]}"
        names = {posixpath.basename(p) for p in self.repo.files}
        close = difflib.get_close_matches(base, sorted(names), n=1, cutoff=0.85)
        if close:
            hit = sorted(self.repo.files_named(close[0]), key=len)[0]
            return f"; did you mean {hit}?"
        return ""

    def check_link(self, line, target):
        if (re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target)
                or target.startswith(("#", "/", "~", "//"))):
            return
        target = unquote(target.split("#", 1)[0].split("?", 1)[0])
        if not target or any(c in target for c in "<>{}$`"):
            return
        self.checked += 1
        full = join(self.doc_dir, target)
        if self.repo.exists(full) or self.repo.is_generated(full) or self.repo.is_ignored(full):
            return
        self.add(line, "SR005",
                 f"link target '{target}' does not exist{self._hint(target, [full])}", target)

    def check_import(self, line, target):
        if target.startswith(("~", "/")):
            return
        name = posixpath.basename(target).lower()
        ext = name.rsplit(".", 1)[1] if "." in name.lstrip(".") else ""
        if not (target.startswith(("./", "../")) or ext in KNOWN_EXTS or name in KNOWN_NAMES):
            return
        self.checked += 1
        full = join(self.doc_dir, target)
        if self.repo.exists(full) or self.repo.is_ignored(full):
            return
        self.add(line, "SR004", f"@-import '{target}' does not exist{self._hint(target)}", target)

    # ---------------------------------------------------------- env vars
    def check_env(self, line, name):
        if name in KNOWN_ENV or name.startswith(KNOWN_ENV_PREFIXES) or len(name) < 3:
            return
        self.checked += 1
        self.env_refs.append((line, name))

    def _env_from_text(self, line, text):
        for rx in ENV_PATTERNS:
            for m in rx.finditer(text):
                self.check_env(line, m.group(1))

    # ---------------------------------------------------------- spans
    def check_span(self, line, text):
        text = text.strip()
        m = shell.PROMPT_RE.match(text)
        if m:
            text = text[m.end():]
        if not text:
            return
        am = ASSIGN_SPAN_RE.match(text)
        if am:
            self.check_env(line, am.group(1))
            return
        found_env = False
        for rx in ENV_PATTERNS:
            for em in rx.finditer(text):
                found_env = True
                self.check_env(line, em.group(1))
        if " " not in text:
            if BARE_ENV_RE.match(text) and not found_env:
                ctx = self._line_text(line)
                if ENV_CONTEXT_RE.search(ctx):
                    self.check_env(line, text)
                return
            if not found_env:
                self.check_path(line, text)
            return
        state = {"cwd": self.doc_dir, "locals": set(), "created": set(), "span": True}
        try:
            for cmd in shell.split_commands(text, line):
                self.check_command(cmd, state, span=True)
        except _Stop:
            pass

    def _line_dirs(self, line, state):
        """Folders named on the same prose line as a command span: "run `npm run dev`
        in `ui/dashboard`" means the script lives in ui/dashboard/package.json."""
        if not state.get("span"):
            return []
        out = []
        for tok in re.findall(r"[\w.@-]+(?:/[\w.@-]+)*/?", self._line_text(line)):
            tok = norm(clean_token(tok).rstrip("/"))
            if not tok or tok.startswith(".."):
                continue
            for b in (self.doc_dir, ""):
                d = join(b, tok)
                if d and d not in out and d in self.repo.dirs:
                    out.append(d)
        return out

    def _sentence(self, line):
        """This line, plus the unfinished sentence on the line before when this one
        continues it: "Never edit\\n`src/gen.py` by hand." is one sentence, but
        "Create `a.yml`.\\nSee `b.yml`." is two."""
        text = self._line_text(line)
        prev = self._line_text(line - 1) if line > 1 else ""
        if prev.strip() and not prev.lstrip().startswith(("#", "```", "~~~", "|")) \
                and not re.match(r"\s*(?:[-*+]|\d+[.)])\s|\s*[#|]", text):
            prose = markdown.HTML_COMMENT_RE.sub("", prev).rstrip() + " "
            tail = re.split(r"[.!?:][`*_)\"']*\s+", prose)[-1]
            if tail.strip():
                return tail.strip() + " " + text
        return text

    def _line_text(self, line):
        if not hasattr(self, "_lines"):
            self._lines = self.repo.read_text(self.rel).splitlines()
        return self._lines[line - 1] if 0 < line <= len(self._lines) else ""

    # ---------------------------------------------------------- blocks
    def check_block(self, block):
        cmds = shell.block_commands(block.lines, block.lang)
        state = {"cwd": self.doc_dir, "locals": set(), "created": set()}
        try:
            for cmd in cmds:
                self.check_command(cmd, state)
        except _Stop:
            pass

    # ---------------------------------------------------------- commands
    def check_command(self, cmd, state, span=False):
        argv = [a for a in cmd.argv]
        line = cmd.line
        if not argv:
            for name, _ in cmd.assigns:  # FOO=bar on its own line: a shell variable
                state["locals"].add(name)
            return
        prog = posixpath.basename(argv[0]).lower()
        if prog.endswith(".exe"):
            prog = prog[:-4]
        known = prog in DISPATCH or prog in GENERIC or PYTHONS.match(prog) \
            or prog in PATH_FLAG_TOOLS \
            or prog in ("export", "set", "setx", "git", "mkdir", "cd", "pushd", "popd",
                        "set-location", "sl")
        path_exec = classify(clean_token(argv[0])) in ("path", "maybe") and "/" in argv[0]
        if not known and not path_exec:
            if span:
                return
            self._note_created(argv, state)  # so a later "cd my-app" is not reported
            return
        for name, _ in cmd.assigns:
            if name not in state["locals"]:
                self.check_env(line, name)
        for a in argv[1:]:
            for rx in ENV_PATTERNS[9:11]:
                for m in rx.finditer(a):
                    if m.group(1) not in state["locals"]:
                        self.check_env(line, m.group(1))
        self._note_created(argv, state)
        if path_exec:
            self.check_path(line, argv[0], state["cwd"])
            return
        handler = DISPATCH.get(prog)
        if handler is None and PYTHONS.match(prog):
            handler = FileChecker.cmd_python
        if handler is not None:
            handler(self, argv, line, state)
        elif prog in GENERIC:
            self.generic_paths(argv, line, state)
        elif prog in PATH_FLAG_TOOLS:
            for i, a in enumerate(argv[:-1]):
                if a in PATH_FLAGS:
                    self.check_path(line, argv[i + 1], state["cwd"])
            for a in argv:
                if "=" in a and a.split("=", 1)[0] in PATH_FLAGS:
                    self.check_path(line, a.split("=", 1)[1], state["cwd"])

    @staticmethod
    def _note_created(argv, state):
        words = {a.lower() for a in argv[1:]}
        if CREATE_WORDS & words or any(w.startswith("create-") for w in words):
            state["created"].add(posixpath.basename(argv[-1].rstrip("/")).removesuffix(".git"))

    def generic_paths(self, argv, line, state):
        args = argv[1:]
        prog = posixpath.basename(argv[0]).lower()
        positional = [a for a in args if not a.startswith("-")]
        if prog in NO_CREATE_CHECK and positional:
            positional = positional[:-1]
        for a in args:
            if a.startswith("--") and "=" in a:
                self.check_path(line, a.split("=", 1)[1], state["cwd"])
        for a in positional:
            self.check_path(line, a, state["cwd"])

    def cmd_export(self, argv, line, state):
        for a in argv[1:]:
            if "=" in a and not a.startswith("-"):
                name = a.split("=", 1)[0]
                self.check_env(line, name)

    def cmd_git(self, argv, line, state):
        if len(argv) > 2 and argv[1] == "clone":
            pos = [a for a in argv[2:] if not a.startswith("-")]
            if pos:
                name = pos[1] if len(pos) > 1 else pos[0].rstrip("/").rsplit("/", 1)[-1]
                state["created"].add(name[:-4] if name.endswith(".git") else name)

    def cmd_mkdir(self, argv, line, state):
        for a in argv[1:]:
            if not a.startswith("-"):
                state["created"].add(norm(a))
                state["created"].add(posixpath.basename(a.rstrip("/")))

    def cmd_cd(self, argv, line, state):
        pos = [a for a in argv[1:] if not a.startswith("-")]
        if not pos:
            raise _Stop()
        target = clean_token(pos[0])
        if not target or target[0] in "~/$%" or ":" in target or any(c in target for c in "<>{}*`"):
            raise _Stop()
        if is_placeholder(target):
            raise _Stop()
        cur = state["cwd"]
        cand = join(cur, target)
        if cand.startswith(".."):
            raise _Stop()
        if self.repo.is_dir(cand):
            state["cwd"] = cand
            return
        if cur and self.repo.is_dir(norm(target)):
            # "cd examples/app" in examples/app/README.md: from the root
            state["cwd"] = norm(target)
            return
        head, _, tail = norm(target).partition("/")
        if tail and head in self.repo.remote_slugs() and self.repo.is_dir(tail):
            state["cwd"] = tail  # "cd litellm/cookbook" right after cloning litellm
            return
        base = posixpath.basename(target.rstrip("/"))
        if base in state["created"] or norm(target) in state["created"]:
            raise _Stop()
        if base in self.repo.remote_slugs():
            state["cwd"] = ""  # "cd <repo-name>" right after cloning it
            return
        if self.repo.is_generated(cand) or self.repo.is_ignored(cand):
            raise _Stop()
        self.checked += 1
        self.add(line, "SR050", f"cd '{target}': no such folder{self._hint(target)}", target)
        raise _Stop()

    def cmd_popd(self, argv, line, state):
        raise _Stop()

    # ---------------------------------------------------------- node
    def cmd_node_pm(self, argv, line, state):
        pm = posixpath.basename(argv[0]).lower().removesuffix(".exe").removesuffix(".cmd")
        args = argv[1:]
        cwd = state["cwd"]
        alt = self._line_dirs(line, state)
        any_ws = False
        positional = []
        i = 0
        value_flags = {"-w", "--workspace", "--filter", "-F", "--scope", "--prefix", "-C", "--dir",
                       "--cwd"}
        while i < len(args):
            a = args[i]
            if a == "--":
                break
            if a in ("--if-present", "--silent-if-missing"):
                return
            if a in ("-r", "--recursive", "-ws", "--workspaces", "-A", "--all"):
                any_ws = True
            elif a in value_flags:
                if a in ("--prefix", "-C", "--dir", "--cwd") and i + 1 < len(args):
                    cwd = join(cwd, args[i + 1])
                else:
                    any_ws = True
                i += 2
                continue
            elif a.startswith("-") and "=" in a:
                key, val = a.split("=", 1)
                if key in ("--prefix", "--dir", "--cwd"):
                    cwd = join(cwd, val)
                elif key in ("--workspace", "--filter", "--scope"):
                    any_ws = True
            elif not a.startswith("-"):
                positional.append(a)
            i += 1
        if not positional:
            return
        sub = positional[0]
        if pm == "yarn" and sub in ("workspace", "workspaces"):
            any_ws = True
            rest = positional[2:] if sub == "workspace" else positional[1:]
            if rest and rest[0] in ("run", "foreach"):
                rest = [r for r in rest[1:] if r != "run"]
            if not rest:
                return
            self._script(pm, rest[0], line, cwd, any_ws, strict=True, allow_bin=True, alt=alt)
            return
        if sub in ("run", "run-script", "rum", "urn"):
            if len(positional) < 2:
                return
            name = positional[1]
            if pm == "bun" and classify(clean_token(name)) in ("path", "bare", "maybe"):
                self.check_path(line, name, cwd)
                return
            self._script(pm, name, line, cwd, any_ws, strict=True, allow_bin=pm in ("yarn", "bun"),
                         alt=alt)
            return
        if pm == "npm":
            if sub in NPM_LIFECYCLE:
                self._script(pm, NPM_LIFECYCLE[sub], line, cwd, any_ws, strict=True,
                             allow_bin=False, start_fallback=sub == "start", alt=alt)
            return
        builtins = {"pnpm": PNPM_BUILTINS, "yarn": YARN_BUILTINS, "bun": BUN_BUILTINS}[pm]
        if sub in ("test", "t", "start") and pm != "bun":
            name = "start" if sub == "start" else "test"
            self._script(pm, name, line, cwd, any_ws, strict=True, allow_bin=False,
                         start_fallback=sub == "start", alt=alt)
            return
        if sub in builtins:
            return
        if pm == "bun" and classify(clean_token(sub)) in ("path", "bare", "maybe"):
            self.check_path(line, sub, cwd)
            return
        self._script(pm, sub, line, cwd, any_ws, strict=False, allow_bin=True, alt=alt)

    def _script(self, pm, name, line, cwd, any_ws, strict, allow_bin, start_fallback=False, alt=()):
        if "$" in name or "<" in name or "{" in name:
            return
        if not name.startswith("@") and ("/" in name or re.search(r"\.[cm]?[jt]sx?$", name)):
            self.check_path(line, name, cwd=cwd)  # "bun scripts/build.ts" runs a file
            return
        self.checked += 1
        repo = self.repo
        if any_ws:
            pkgs = repo.node_packages()
            if any(name in repo.scripts(p) for p in pkgs):
                return
            if not pkgs:
                self.add(line, "SR011",
                         f"'{pm} ... {name}' but there is no package.json in the repo", name)
                return
            scripts = set().union(*(repo.scripts(p) for p in pkgs))
            where = "any workspace package.json"
            pkg = None
        else:
            pkg = repo.nearest(cwd, ["package.json"])
            if pkg is None:
                self.add(line, "SR011",
                         f"'{pm} {name}' but no package.json in {cwd or '.'} or above", name)
                return
            scripts = repo.scripts(pkg)
            if name in scripts:
                return
            for d in alt:
                other = repo.nearest(d, ["package.json"])
                if other and name in repo.scripts(other):
                    return
            up = posixpath.dirname(posixpath.dirname(pkg)) if posixpath.dirname(pkg) else None
            # "bun run lint" in packages/x/AGENTS.md often means the root script
            while up is not None:
                other = repo.nearest(up, ["package.json"])
                if not other:
                    break
                if name in repo.scripts(other):
                    return
                up = (posixpath.dirname(posixpath.dirname(other))
                      if posixpath.dirname(other) else None)
            where = pkg
            if not scripts and any(name in repo.scripts(p) for p in repo.node_packages()):
                return  # this package.json has no scripts; the doc means another package
        if start_fallback and pkg and repo.is_file(join(posixpath.dirname(pkg), "server.js")):
            return
        if allow_bin and self._node_bin(name, pkg):
            return
        rule = "SR010" if strict else "SR012"
        close = difflib.get_close_matches(name, sorted(scripts), n=1, cutoff=0.6)
        if close:
            hint = f"; did you mean '{close[0]}'?"
        elif scripts:
            listed = sorted(scripts)
            hint = "; scripts: " + ", ".join(listed[:8]) + (", ..." if len(listed) > 8 else "")
        else:
            hint = "; it has no scripts"
        what = ("is not a script, built-in or installed package in" if rule == "SR012"
                else "is not a script in")
        self.add(line, rule, f"{pm} script '{name}' {what} {where}{hint}", name)

    def _node_bin(self, name, pkg):
        deps, bins = self.repo.node_deps()
        if name in deps or name in bins or NODE_BIN_PACKAGES.get(name) in deps:
            return True
        if any(d.split("/")[-1] == name for d in deps):
            return True
        base = posixpath.dirname(pkg) if pkg else ""
        for b in (base, ""):
            if self.repo.exists(join(b, "node_modules/.bin/" + name)):
                return True
        return False

    # ---------------------------------------------------------- make / just / task
    def cmd_make(self, argv, line, state):
        cwd = state["cwd"]
        makefile = None
        targets = []
        args = argv[1:]
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-C", "--directory") and i + 1 < len(args):
                cwd = join(cwd, args[i + 1])
                i += 2
                continue
            if a in ("-f", "--file", "--makefile") and i + 1 < len(args):
                makefile = join(cwd, args[i + 1])
                i += 2
                continue
            if a.startswith(("--directory=", "-C")) and len(a) > 2 and a != "-C":
                cwd = join(cwd, a.split("=", 1)[1] if "=" in a else a[2:])
            elif a.startswith(("--file=", "--makefile=")):
                makefile = join(cwd, a.split("=", 1)[1])
            elif (a in ("-I", "-o", "-W", "--include-dir", "--old-file", "--what-if", "--eval")
                  and i + 1 < len(args)):
                i += 2
                continue
            elif (a in ("-j", "--jobs", "-l", "--load-average") and i + 1 < len(args)
                  and args[i + 1].isdigit()):
                i += 2
                continue
            elif a in ("--version", "--help", "-v", "-h"):
                return
            elif not a.startswith("-") and "=" not in a:
                if any(c in a for c in "*?[<{$"):
                    return  # "make examples-*": a family of targets, not one name
                targets.append(a)
            i += 1
        alt = []
        if makefile is None:
            names = ("GNUmakefile", "makefile", "Makefile")
            makefile = next((join(cwd, n) for n in names if self.repo.is_file(join(cwd, n))), None)
            if makefile is None and cwd == self.doc_dir and cwd == state["cwd"]:
                # A doc in a subfolder usually means "run this from the repo root".
                makefile = self.repo.nearest(cwd, names)
            alt = [m for d in self._line_dirs(line, state)
                   for m in (next((join(d, n) for n in names if self.repo.is_file(join(d, n))),
                                  None),)
                   if m]
            if makefile is None and alt:
                makefile = alt[0]
            if makefile is None and targets and all(self._defined_below(t) for t in targets):
                self.checked += len(targets)
                return  # a monorepo guide: "make test" inside each package folder
            if makefile is None:
                self.checked += 1
                self.add(line, "SR021", f"'make' but there is no Makefile in {cwd or '.'}", "make")
                return
        elif not self.repo.is_file(makefile):
            self.check_path(line, makefile, "", kind="path")
            return
        defined, patterns, dynamic = self.repo.make_targets(makefile)
        for t in targets:
            if "$" in t or "<" in t:
                continue
            self.checked += 1
            if t in defined or dynamic or any(re.fullmatch(re.escape(p).replace("%", ".+"), t)
                                              for p in patterns):
                continue
            if self.repo.exists(join(posixpath.dirname(makefile), t)):
                continue  # make builds a file that already exists
            if any(t in self.repo.make_targets(m)[0] or self.repo.make_targets(m)[2] for m in alt):
                continue
            close = difflib.get_close_matches(t, sorted(defined), n=1, cutoff=0.6)
            hint = f"; did you mean '{close[0]}'?" if close else ""
            self.add(line, "SR020", f"make target '{t}' is not defined in {makefile}{hint}", t)

    def _defined_below(self, target):
        """True if some Makefile under this doc's folder defines `target`."""
        for m in self.repo.all_named(("Makefile", "makefile", "GNUmakefile")):
            if ((not self.doc_dir or m.startswith(self.doc_dir + "/"))
                    and not self.repo.is_generated(m)):
                defined, _, dynamic = self.repo.make_targets(m)
                if target in defined:
                    return True
        return False

    def cmd_just(self, argv, line, state):
        cwd = state["cwd"]
        args = argv[1:]
        justfile = None
        positional = []
        i = 0
        while i < len(args):
            a = args[i]
            if a in ("-f", "--justfile") and i + 1 < len(args):
                justfile = join(cwd, args[i + 1])
                i += 2
                continue
            if a in ("-d", "--working-directory", "--set", "--dotenv-path", "--dotenv-filename",
                     "--color", "--shell", "--shell-arg", "--command", "-c",
                     "--chooser") and i + 1 < len(args):
                i += 3 if a == "--set" else 2
                continue
            if a in ("--version", "--help", "-h", "-V", "--init", "--completions", "--man",
                     "--changelog"):
                return
            if a.startswith("-"):
                i += 1
                continue
            positional.append(a)
            i += 1
        names = ("justfile", "Justfile", ".justfile", "JUSTFILE")
        if justfile is None:
            justfile = self.repo.nearest(cwd, names)
            if justfile is None:
                self.checked += 1
                self.add(line, "SR023",
                         f"'just' but there is no justfile in {cwd or '.'} or above", "just")
                return
        recipes, modules, fallback = self.repo.just_recipes(justfile)
        if fallback:
            parent = (posixpath.dirname(posixpath.dirname(justfile))
                      if posixpath.dirname(justfile) else None)
            while parent is not None:
                up = self.repo.nearest(parent, names)
                if not up:
                    break
                r, m, fb = self.repo.just_recipes(up)
                recipes, modules = recipes | r, modules | m
                parent = (posixpath.dirname(posixpath.dirname(up))
                          if posixpath.dirname(up) and fb else None)
        positional = [p for p in positional if "=" not in p]
        if not positional:
            return
        name = positional[0]
        if "$" in name or "<" in name:
            return
        self.checked += 1
        if name in recipes or name in modules or name.split("::")[0] in modules:
            return
        close = difflib.get_close_matches(name, sorted(recipes), n=1, cutoff=0.6)
        hint = f"; did you mean '{close[0]}'?" if close else ""
        self.add(line, "SR022", f"just recipe '{name}' is not defined in {justfile}{hint}", name)

    def cmd_task(self, argv, line, state):
        cwd = state["cwd"]
        args = argv[1:]
        taskfile = None
        positional = []
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--":
                break
            if a in ("-t", "--taskfile") and i + 1 < len(args):
                taskfile = join(cwd, args[i + 1])
                i += 2
                continue
            if a in ("-d", "--dir") and i + 1 < len(args):
                cwd = join(cwd, args[i + 1])
                i += 2
                continue
            if a in ("--version", "--help", "-h", "--init", "-i"):
                return
            if not a.startswith("-") and "=" not in a:
                positional.append(a)
            i += 1
        names = ("Taskfile.yml", "taskfile.yml", "Taskfile.yaml", "taskfile.yaml",
                 "Taskfile.dist.yml", "taskfile.dist.yml", "Taskfile.dist.yaml",
                 "taskfile.dist.yaml")
        if taskfile is None:
            taskfile = self.repo.nearest(cwd, names)
            if taskfile is None:
                self.checked += 1
                self.add(line, "SR025",
                         f"'task' but there is no Taskfile.yml in {cwd or '.'} or above", "task")
                return
        tasks, includes = self.repo.task_names(taskfile)
        for name in positional:
            if "$" in name or "<" in name or "{" in name:
                continue
            self.checked += 1
            if name in tasks or name.split(":")[0] in includes:
                continue
            if any(re.fullmatch(re.escape(t).replace(r"\*", ".+"), name)
                   for t in tasks if "*" in t):
                continue
            close = difflib.get_close_matches(name, sorted(tasks), n=1, cutoff=0.6)
            hint = f"; did you mean '{close[0]}'?" if close else ""
            self.add(line, "SR024", f"task '{name}' is not defined in {taskfile}{hint}", name)

    # ---------------------------------------------------------- python
    def cmd_python(self, argv, line, state):
        args = argv[1:]
        cwd = state["cwd"]
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-m" and i + 1 < len(args):
                self._module(args[i + 1], line, cwd)
                if args[i + 1].split(".")[0] in PYTHON_M_PATH_MODULES:
                    self.generic_paths(["x"] + args[i + 2:], line, state)
                return
            if a.startswith("-m") and len(a) > 2:
                self._module(a[2:], line, cwd)
                return
            if a in ("-c", "-"):
                return
            if a in ("-W", "-X", "-Q"):
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            tok = clean_token(a)
            kind = classify(tok)
            if kind is None and tok.endswith((".py", ".pyw", ".pyz")):
                kind = "bare"
            if kind:
                self.check_path(line, a, cwd, kind=kind)
            return

    def _module(self, mod, line, cwd):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", mod):
            return
        parts = mod.split(".")
        top = parts[0]
        repo = self.repo
        base = None
        for b in self._bases(cwd) + [join(cwd, "src"), "src"]:
            pkg = join(b, top)
            if repo.is_file(join(pkg, "__init__.py")) or (b.endswith("src") and repo.is_dir(pkg)):
                base = pkg
                break
            if repo.is_file(pkg + ".py") and len(parts) == 1:
                return  # python -m module.py-style single file in the repo
            if repo.is_file(pkg + ".py"):
                base = pkg + ".py"
                break
        if base is None:
            return  # not the repo's own package: stdlib or installed
        self.checked += 1
        cur = base
        for seg in parts[1:]:
            if cur.endswith(".py"):
                self.add(line, "SR030",
                         f"python -m {mod}: {cur} is a module, so it has no '{seg}'", mod)
                return
            if repo.is_dir(join(cur, seg)):
                cur = join(cur, seg)
            elif repo.is_file(join(cur, seg + ".py")):
                cur = join(cur, seg + ".py")
            else:
                self.add(line, "SR030",
                         f"python -m {mod}: no {join(cur, seg)}.py or {join(cur, seg)}/"
                         " in the repo", mod)
                return
        if not cur.endswith(".py") and not repo.is_file(join(cur, "__main__.py")):
            self.add(line, "SR031",
                     f"python -m {mod}: {cur}/ has no __main__.py, so it cannot be run with -m",
                     mod)

    def cmd_py_runner(self, argv, line, state):
        tool = posixpath.basename(argv[0]).lower()
        args = argv[1:]
        cwd = state["cwd"]
        if tool == "uv" and args[:1] == ["pip"]:
            self.generic_flags(args, line, cwd)
            return
        if not args or args[0] != "run":
            return
        rest = args[1:]
        has_with = False
        i = 0
        value_flags = {"--with", "--with-editable", "--with-requirements", "--extra", "--group",
                       "--package", "-p", "--python", "--project", "--index", "--index-url",
                       "--env", "-e", "--only-group"}
        while i < len(rest):
            a = rest[i]
            if a == "--":
                i += 1
                break
            if a in ("--directory", "--project") and i + 1 < len(rest):
                cwd = join(cwd, rest[i + 1])
                i += 2
                continue
            if a == "--env-file" and i + 1 < len(rest):
                self.check_path(line, rest[i + 1], cwd)
                i += 2
                continue
            if a in value_flags:
                has_with = has_with or a.startswith("--with")
                i += 2
                continue
            if a.startswith("--with"):
                has_with = True
            if a in ("-m", "--module"):
                self.cmd_python(["python"] + rest[i:], line, dict(state, cwd=cwd))
                return
            if not a.startswith("-"):
                break
            i += 1
        target = rest[i:] if i < len(rest) else []
        if not target:
            return
        x = target[0]
        if PYTHONS.match(x.lower()):
            self.cmd_python(target, line, dict(state, cwd=cwd))
            return
        tok = clean_token(x)
        kind = classify(tok)
        if kind in ("path", "bare", "maybe") or tok.endswith(".py"):
            self.check_path(line, x, cwd, kind=kind or "bare")
            return
        if has_with or "$" in x or "<" in x or x in NOT_PYTHON_TOOLS:
            return
        self.checked += 1
        scripts, deps, hatch = self.repo.python_info()
        name = x.split(":", 1)[-1] if tool == "hatch" else x
        if x in scripts or x in hatch or name in hatch or canon(name) in deps or name in PY_ALWAYS:
            pass
        elif PY_BIN_PACKAGES.get(name) and canon(PY_BIN_PACKAGES[name]) in deps:
            pass
        else:
            self.add(line, "SR032",
                     f"{tool} run {x}: not a script or dependency declared in the repo", x)
            return
        if name in ("pytest", "py.test", "mypy", "ruff", "black"):
            self.generic_paths(["x"] + target[1:], line, dict(state, cwd=cwd))

    def generic_flags(self, args, line, cwd):
        for i, a in enumerate(args[:-1]):
            if a in PATH_FLAGS:
                self.check_path(line, args[i + 1], cwd)


DISPATCH = {
    "npm": FileChecker.cmd_node_pm, "pnpm": FileChecker.cmd_node_pm,
    "yarn": FileChecker.cmd_node_pm, "bun": FileChecker.cmd_node_pm,
    "make": FileChecker.cmd_make, "gmake": FileChecker.cmd_make,
    "just": FileChecker.cmd_just, "task": FileChecker.cmd_task, "go-task": FileChecker.cmd_task,
    "uv": FileChecker.cmd_py_runner, "poetry": FileChecker.cmd_py_runner,
    "pdm": FileChecker.cmd_py_runner, "hatch": FileChecker.cmd_py_runner,
    "pipenv": FileChecker.cmd_py_runner, "rye": FileChecker.cmd_py_runner,
    "export": FileChecker.cmd_export, "setx": FileChecker.cmd_export, "git": FileChecker.cmd_git,
    "mkdir": FileChecker.cmd_mkdir, "cd": FileChecker.cmd_cd, "pushd": FileChecker.cmd_cd,
    "set-location": FileChecker.cmd_cd, "sl": FileChecker.cmd_cd, "popd": FileChecker.cmd_popd,
}
