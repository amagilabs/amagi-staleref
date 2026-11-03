"""What exists in the repo: files, folders, scripts, targets, recipes, tasks, deps."""

import atexit
import json
import os
import posixpath
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from functools import lru_cache, partial, wraps

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - exercised on 3.9/3.10 only
    import tomli as tomllib

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".tox", ".nox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache",
    "dist", "build", "target", "coverage", ".gradle", ".idea", ".terraform",
}
# Folders that hold build output or installed packages. A reference into one
# of these is about something generated, so it is never reported.
GENERATED_DIRS = SKIP_DIRS | {"out", "bin/Debug", "bin/Release", "obj", "vendor", "site",
                              "_build", "htmlcov", "logs", "tmp", "output", "outputs", "results",
                              "artifacts"}

TEXT_SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".pdf", ".zip", ".gz", ".tgz",
    ".7z", ".rar", ".exe", ".dll", ".so", ".dylib", ".pyc", ".whl", ".db", ".sqlite", ".mp4",
    ".mp3", ".mov", ".wav", ".woff", ".woff2", ".ttf", ".eot", ".otf", ".bin", ".jar", ".class",
    ".lockb", ".parquet", ".npy", ".pkl", ".svgz", ".avif", ".heic", ".psd", ".ai", ".sketch",
}
MAX_TEXT_BYTES = 2_000_000
# Stop listing a tree past this many files (0 = no limit). A folder that holds
# many projects, or a home folder, is not one repo.
DEFAULT_MAX_FILES = 100_000
# A folder holding one of these files is not source: a virtualenv of any name
# (pyvenv.cfg), a cache that says so (CACHEDIR.TAG, https://bford.info/cachedir/),
# or a Chromium/Chrome/Edge user-data folder ("Local State").
SKIP_MARKERS = ("pyvenv.cfg", "CACHEDIR.TAG", "Local State")
ENV_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".tmpl", ".in")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")


def norm(rel):
    """Normalise a repo-relative posix path. Returns '' for the root."""
    rel = posixpath.normpath(rel.replace("\\", "/"))
    return "" if rel == "." else rel


def join(base, rel):
    return norm(posixpath.join(base, rel)) if base else norm(rel)


def glob_to_regex(pattern):
    """Translate a gitignore-ish glob (**, *, ?, [..]) into a compiled regex."""
    i, out = 0, []
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                body = pattern[i + 1:j].replace("\\", "\\\\")
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "/?$")


def expand_braces(pattern):
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    out = []
    for alt in m.group(1).split(","):
        out.extend(expand_braces(pattern[:m.start()] + alt + pattern[m.end():]))
    return out


def close_match(word, pool, cutoff):
    """difflib.get_close_matches(word, pool, n=1, cutoff=cutoff)[0], or None,
    without building a result list. Ties go to the larger string, as in difflib."""
    best = None
    s = SequenceMatcher()
    s.set_seq2(word)
    for x in pool:
        s.set_seq1(x)
        if s.real_quick_ratio() >= cutoff and s.quick_ratio() >= cutoff:
            r = s.ratio()
            if r >= cutoff and (best is None or (r, x) > best):
                best = (r, x)
    return best[1] if best else None


def _run_git(root, args):
    try:
        res = subprocess.run(
            ["git", "-C", root] + args, capture_output=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return res.stdout


def _pep508_name(spec):
    m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec or "")
    return canon(m.group(1)) if m else None


def canon(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _tool_dependencies(node, depth=0):
    """Every string in a `dependencies`-style list anywhere under [tool]."""
    out = set()
    if depth > 6 or not isinstance(node, dict):
        return out
    for key, val in node.items():
        if key.endswith("dependencies") and isinstance(val, list):
            out.update(_pep508_name(s) for s in val if isinstance(s, str))
        elif key.endswith("dependencies") and isinstance(val, dict):
            for group in val.values():
                if isinstance(group, list):
                    out.update(_pep508_name(s) for s in group if isinstance(s, str))
        elif isinstance(val, dict):
            out |= _tool_dependencies(val, depth + 1)
    return out


def _load_toml(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError):
        return {}


def _instance_cache(maxsize):
    """lru_cache kept on the instance, so it is freed with the Repo (ruff B019)."""
    def decorate(method):
        attr = "_cache_" + method.__name__

        @wraps(method)
        def wrapper(self, *args, **kwargs):
            cached = self.__dict__.get(attr)
            if cached is None:
                cached = self.__dict__[attr] = lru_cache(maxsize=maxsize)(partial(method, self))
            return cached(*args, **kwargs)
        return wrapper
    return decorate


class TooManyFiles(Exception):
    """The tree holds more files than the --max-files limit."""

    def __init__(self, root, limit):
        super().__init__(f"{root} holds more than {limit:,} files")
        self.root, self.limit = root, limit


class GitIgnore:
    """The .gitignore rules for a tree that is not a git checkout.

    Supports what most .gitignore files use: one file per folder, `#` comments,
    `!` negation, a trailing `/` for folders only, a leading or middle `/` to
    anchor to the file's folder, and `*`, `?`, `[..]`, `**`. Last match wins.
    """

    def __init__(self, root):
        self.root = root
        self._rules = {}  # folder -> [(regex, anchored, dir_only, negate)]

    def rules_in(self, rel_dir):
        if rel_dir not in self._rules:
            rules = []
            folder = os.path.join(self.root, *rel_dir.split("/")) if rel_dir else self.root
            lines = []
            # .git/info/exclude of a repo nested in the tree, then its .gitignore (which wins).
            for path in (os.path.join(folder, ".git", "info", "exclude"),
                         os.path.join(folder, ".gitignore")):
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        lines += fh.read().splitlines()
                except OSError:
                    pass
            for line in lines:
                line = line.rstrip()
                if not line or line.startswith("#"):
                    continue
                negate = line.startswith("!")
                if negate or line.startswith("\\"):
                    line = line[1:]
                dir_only = line.endswith("/")
                line = line.rstrip("/")
                anchored = "/" in line
                line = line.lstrip("/")
                if line:
                    rules.append((glob_to_regex(line), anchored, dir_only, negate))
            self._rules[rel_dir] = rules
        return self._rules[rel_dir]

    def decide(self, rel, is_dir):
        """True ignored, False re-included, None when no rule matches.
        Reads only the .gitignore files in the folders above `rel`."""
        parent = posixpath.dirname(rel)
        chain = [""]
        if parent:
            parts = parent.split("/")
            chain += ["/".join(parts[:i + 1]) for i in range(len(parts))]
        for base in reversed(chain):
            sub = rel[len(base) + 1:] if base else rel
            for rx, anchored, dir_only, negate in reversed(self.rules_in(base)):
                if dir_only and not is_dir:
                    continue
                if rx.match(sub if anchored else posixpath.basename(sub)):
                    return not negate
        return None

    def ignored(self, rel, is_dir):
        """`rel` or a folder above it is ignored."""
        parts = rel.split("/")
        for i in range(1, len(parts) + 1):
            last = i == len(parts)
            if self.decide("/".join(parts[:i]), is_dir if last else True):
                return True
        return False


class Repo:
    def __init__(self, root, max_files=DEFAULT_MAX_FILES, progress=None):
        self.root = os.path.abspath(root)
        self.files = set()
        self.dirs = {""}
        self.git = False
        self.skipped = []  # folders left out of the listing because of SKIP_MARKERS
        self.max_files = max_files or 0
        self._progress = progress
        self._gitignore = GitIgnore(self.root)
        self._list_files()
        self._by_name = {}
        for f in self.files:
            self._by_name.setdefault(posixpath.basename(f).lower(), []).append(f)
        self._env = None
        self._ignore_proc = None
        self._exact_names = None
        self._children = None
        self._names_by_len = None

    # ---------------------------------------------------------------- listing
    def _list_files(self):
        out = _run_git(self.root, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
        if out is not None:
            deleted = _run_git(self.root, ["ls-files", "-z", "--deleted"]) or b""
            gone = set(deleted.decode("utf-8", "replace").split("\0"))
            self.git = True
            for rel in out.decode("utf-8", "replace").split("\0"):
                if rel and rel not in gone:
                    self._add(rel)
            self._check_limit()
            return
        start, shown = time.monotonic(), 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_dir = os.path.relpath(dirpath, self.root).replace("\\", "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            prefix = rel_dir + "/" if rel_dir else ""
            keep = []
            for d in dirnames:
                if (d in SKIP_DIRS or d.endswith(".egg-info")
                        or self._gitignore.ignored(prefix + d, True)):
                    continue
                full = os.path.join(dirpath, d)
                if any(os.path.isfile(os.path.join(full, m)) for m in SKIP_MARKERS):
                    self.skipped.append(prefix + d)
                    continue
                keep.append(d)
            dirnames[:] = keep
            for name in filenames:
                # A file named .git marks a worktree; it is not source.
                if name != ".git" and not self._gitignore.ignored(prefix + name, False):
                    self._add(prefix + name)
            self._check_limit()
            if self._progress and time.monotonic() - start > 2 + shown * 5:
                shown += 1
                self._progress(f"listing files: {len(self.files):,} so far ({rel_dir or '.'})")

    def _check_limit(self):
        if self.max_files and len(self.files) > self.max_files:
            raise TooManyFiles(self.root, self.max_files)

    def _add(self, rel):
        rel = rel.replace("\\", "/")
        self.files.add(rel)
        parent = posixpath.dirname(rel)
        while parent and parent not in self.dirs:
            self.dirs.add(parent)
            parent = posixpath.dirname(parent)

    def abspath(self, rel):
        return os.path.join(self.root, *rel.split("/")) if rel else self.root

    # ------------------------------------------------------------- existence
    def exists(self, rel):
        rel = norm(rel)
        if rel in self.files or rel in self.dirs:
            return True
        return self._on_disk(rel, os.path.exists)

    def is_dir(self, rel):
        rel = norm(rel)
        return rel in self.dirs or self._on_disk(rel, os.path.isdir)

    def is_file(self, rel):
        rel = norm(rel)
        return rel in self.files or self._on_disk(rel, os.path.isfile)

    def _on_disk(self, rel, test):
        """Filesystem fallback (ignored or untracked files) that is case-exact even on
        Windows and macOS, so 'makefile' does not match 'Makefile'."""
        if not test(self.abspath(rel)):
            return False
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        cur = self.root
        for part in parts:
            if part == "..":
                cur = os.path.dirname(cur)
                continue
            if part not in self._listdir(cur):
                return False
            cur = os.path.join(cur, part)
        return True

    @_instance_cache(maxsize=4096)
    def _listdir(self, path):
        try:
            return frozenset(os.listdir(path))
        except OSError:
            return frozenset()

    def files_named(self, name):
        return self._by_name.get(name.lower(), [])

    # Indexes built on first use, so a clean repo never pays for them.
    def _paths_by_exact_name(self):
        """Case-exact basename -> every file and folder with that name."""
        if self._exact_names is None:
            self._exact_names = {}
            for p in self.files | self.dirs:
                if p:
                    self._exact_names.setdefault(posixpath.basename(p), []).append(p)
        return self._exact_names

    def children(self, rel_dir):
        """Names of the files and folders directly inside rel_dir ('' = root)."""
        if self._children is None:
            self._children = {}
            for p in self.files | self.dirs:
                if p:
                    kids = self._children.setdefault(posixpath.dirname(p), set())
                    kids.add(posixpath.basename(p))
        return self._children.get(rel_dir, set())

    @_instance_cache(maxsize=1024)
    def close_file_name(self, name, cutoff=0.85):
        """The file name in the repo closest to `name`, as difflib.get_close_matches
        would pick it, or None. Only names whose length can reach the cutoff are compared."""
        if self._names_by_len is None:
            self._names_by_len = {}
            for n in {posixpath.basename(p) for p in self.files}:
                self._names_by_len.setdefault(len(n), []).append(n)
        lb = len(name)
        pool = []
        for la, names in self._names_by_len.items():
            if la + lb and 2.0 * min(la, lb) / (la + lb) >= cutoff:
                pool.extend(names)
        return close_match(name, pool, cutoff)

    def suffix_match(self, rel):
        rel = norm(rel).rstrip("/")
        tail = "/" + rel
        same = self._paths_by_exact_name().get(posixpath.basename(rel), [])
        return [p for p in same if p.endswith(tail)]

    def glob(self, pattern, base=""):
        """Repo paths matching a glob, relative to base. Patterns without a / match by basename."""
        hits = []
        for pat in expand_braces(pattern):
            if pat.startswith("./"):
                pat = pat[2:]
            if "/" not in pat.rstrip("/"):
                rx = glob_to_regex(pat)
                for name, paths in self._paths_by_exact_name().items():
                    if rx.match(name):
                        hits.extend(paths)
                continue
            full = join(base, pat) if not pat.startswith("**") else pat
            rx = glob_to_regex(full)
            hits.extend(p for p in self.files | self.dirs if rx.match(p))
        return hits

    @_instance_cache(maxsize=1)
    def remote_slugs(self):
        """owner/repo names from .git/config remotes, plus the folder name."""
        out = {posixpath.basename(self.root.replace("\\", "/").rstrip("/"))}
        try:
            config = os.path.join(self.root, ".git", "config")
            with open(config, encoding="utf-8", errors="replace") as fh:
                url_re = r"url\s*=\s*\S*?[:/]([\w.-]+/[\w.-]+?)(?:\.git)?\s*$"
                for m in re.finditer(url_re, fh.read(), re.M):
                    out.add(m.group(1))
                    out.add(m.group(1).split("/")[1])
        except OSError:
            pass
        return out

    def is_generated(self, rel):
        rel = norm(rel)
        parts = rel.split("/")
        for i in range(len(parts)):
            if parts[i] in GENERATED_DIRS or "/".join(parts[i:i + 2]) in GENERATED_DIRS:
                return True
            if parts[i].endswith(".egg-info"):
                return True
        return False

    @_instance_cache(maxsize=4096)
    def is_ignored(self, rel):
        rel = norm(rel)
        if not rel or rel.startswith(".."):
            return False
        if self.git:
            # "rel/x" catches folder-only patterns such as "build/" for a folder that is not there.
            return any(self._check_ignore(p) for p in (rel, rel + "/x"))
        # The path may name a folder that is not there, so try it both ways.
        return self._gitignore.ignored(rel, False) or self._gitignore.ignored(rel, True)

    def _check_ignore(self, rel):
        """Ask one long-running `git check-ignore --stdin` instead of starting git per path."""
        if self._ignore_proc is None:
            try:
                self._ignore_proc = subprocess.Popen(
                    ["git", "-C", self.root, "check-ignore", "--stdin", "--no-index", "-n", "-v"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    env=dict(os.environ, GIT_FLUSH="1"),
                )
                atexit.register(self.close)
            except OSError:
                self._ignore_proc = False
        if self._ignore_proc and self._ignore_proc.poll() is None:
            try:
                self._ignore_proc.stdin.write(rel.encode("utf-8") + b"\n")
                self._ignore_proc.stdin.flush()
                out = self._ignore_proc.stdout.readline()
                return bool(out) and not out.startswith(b"::")
            except OSError:
                self._ignore_proc = False
        try:
            res = subprocess.run(["git", "-C", self.root, "check-ignore", "-q", "--no-index", rel],
                                 capture_output=True, timeout=30)
            return res.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def close(self):
        proc, self._ignore_proc = self._ignore_proc, False
        if proc:
            try:
                proc.stdin.close()
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                proc.kill()

    # ------------------------------------------------------------ discovery
    def nearest(self, start, names):
        """Walk from folder `start` up to the root; return the first file found from `names`."""
        cur = norm(start)
        while True:
            if cur.startswith(".."):
                cur = ""
            for name in names:
                cand = join(cur, name)
                if self.is_file(cand):
                    return cand
            if not cur:
                return None
            cur = posixpath.dirname(cur)

    def all_named(self, names):
        lowered = {n.lower() for n in names}
        return sorted(f for f in self.files if posixpath.basename(f).lower() in lowered)

    def read_text(self, rel):
        try:
            with open(self.abspath(rel), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    # ----------------------------------------------------------- package.json
    @_instance_cache(maxsize=256)
    def package_json(self, rel):
        try:
            data = json.loads(self.read_text(rel) or "{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def scripts(self, rel):
        s = self.package_json(rel).get("scripts")
        return s if isinstance(s, dict) else {}

    def node_packages(self):
        return [p for p in self.all_named(["package.json"]) if not self.is_generated(p)]

    @_instance_cache(maxsize=1)
    def node_deps(self):
        deps, bins = set(), set()
        for p in self.node_packages():
            data = self.package_json(p)
            for key in ("dependencies", "devDependencies", "optionalDependencies",
                        "peerDependencies"):
                if isinstance(data.get(key), dict):
                    deps.update(data[key])
            b = data.get("bin")
            if isinstance(b, dict):
                bins.update(b)
            elif isinstance(b, str) and data.get("name"):
                bins.add(data["name"].split("/")[-1])
        return deps, bins

    # --------------------------------------------------------------- Makefile
    @_instance_cache(maxsize=64)
    def make_targets(self, rel, _depth=0):
        """(targets, patterns, dynamic) for one Makefile, following include lines."""
        targets, patterns, dynamic = set(), set(), False
        text = self.read_text(rel)
        base = posixpath.dirname(rel)
        in_define = False
        for line in text.splitlines():
            if in_define:
                if line.strip().startswith("endef"):
                    in_define = False
                continue
            if line.startswith("\t") or not line.strip() or line.lstrip().startswith("#"):
                continue
            stripped = line.strip()
            if stripped.startswith("define "):
                in_define = True
                continue
            inc = re.match(r"^\s*-?s?include\s+(.+)$", line)
            if inc:
                for name in inc.group(1).split():
                    if "$" in name:
                        dynamic = True
                        continue
                    hits = (self.glob(name, base) if any(c in name for c in "*?[")
                            else [join(base, name)])
                    for hit in hits:
                        if self.is_file(hit) and _depth < 5:
                            t, p, d = self.make_targets(hit, _depth + 1)
                            targets |= t
                            patterns |= p
                            dynamic = dynamic or d
                continue
            m = re.match(r"^([^:#=\t][^:#=]*?)\s*(::?)(?!=)(.*)$", line)
            if not m:
                continue
            head, rest = m.group(1), m.group(3)
            if re.match(r"^\s*[:+?!]?=", rest):  # VAR := value, VAR ::= value
                continue
            for t in head.split():
                if "$" in t:
                    dynamic = True
                elif "%" in t:
                    patterns.add(t)
                elif t.startswith("."):
                    if t == ".PHONY":
                        targets.update(x for x in rest.split(";")[0].split() if "$" not in x)
                else:
                    targets.add(t)
        return targets, patterns, dynamic

    # --------------------------------------------------------------- justfile
    @_instance_cache(maxsize=64)
    def just_recipes(self, rel, _depth=0):
        """(recipes, modules, fallback) for one justfile, following imports."""
        recipes, modules, fallback = set(), set(), False
        base = posixpath.dirname(rel)
        for line in self.read_text(rel).splitlines():
            if not line or line[0] in " \t#[":
                continue
            m = re.match(r"^import\??\s+['\"]([^'\"]+)['\"]", line)
            if m:
                target = join(base, m.group(1))
                if self.is_file(target) and _depth < 5:
                    r, mo, fb = self.just_recipes(target, _depth + 1)
                    recipes |= r
                    modules |= mo
                continue
            m = re.match(r"^mod\??\s+([A-Za-z_][A-Za-z0-9_-]*)", line)
            if m:
                modules.add(m.group(1))
                continue
            m = re.match(r"^alias\s+([A-Za-z_][A-Za-z0-9_-]*)\s*:=", line)
            if m:
                recipes.add(m.group(1))
                continue
            if re.match(r"^set\s+fallback(\s*:=\s*true)?\s*$", line):
                fallback = True
                continue
            if re.match(r"^(set|export)\s", line):
                continue
            m = re.match(r"^@?([A-Za-z_][A-Za-z0-9_-]*)(?:\s+[^:]*)?:(?!=)", line)
            if m:
                recipes.add(m.group(1))
        return recipes, modules, fallback

    # --------------------------------------------------------------- Taskfile
    @_instance_cache(maxsize=64)
    def task_names(self, rel):
        """(tasks, include_namespaces) from a Taskfile.yml, without a YAML parser."""
        tasks, includes = set(), set()
        section, indent = None, None
        for line in self.read_text(rel).splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not line[0].isspace():
                key = line.split(":", 1)[0].strip()
                section, indent = key, None
                continue
            if section not in ("tasks", "includes"):
                continue
            lead = len(line) - len(line.lstrip())
            if indent is None:
                indent = lead
            if lead == indent:
                m = re.match(r"^\s*['\"]?([^'\":]+(?::[^'\":\s]+)*)['\"]?\s*:", line)
                if m:
                    (tasks if section == "tasks" else includes).add(m.group(1).strip())
            elif section == "tasks":
                m = re.match(r"^\s*aliases\s*:\s*\[(.*)\]", line)
                if m:
                    tasks.update(a.strip().strip("'\"") for a in m.group(1).split(",") if a.strip())
        return tasks, includes

    # ------------------------------------------------------------- Python
    @_instance_cache(maxsize=1)
    def python_info(self):
        """Union of console scripts, runner scripts and dependency names across the repo."""
        scripts, deps, hatch = set(), set(), set()
        for p in self.all_named(["pyproject.toml"]):
            if self.is_generated(p):
                continue
            data = _load_toml(self.abspath(p))
            proj = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
            tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
            for key in ("scripts", "gui-scripts"):
                if isinstance(proj.get(key), dict):
                    scripts.update(proj[key])
            if proj.get("name"):
                deps.add(canon(str(proj["name"])))
            for spec in proj.get("dependencies", []) or []:
                deps.add(_pep508_name(spec))
            for group in (proj.get("optional-dependencies") or {}).values():
                deps.update(_pep508_name(s) for s in group if isinstance(s, str))
            for group in (data.get("dependency-groups") or {}).values():
                deps.update(_pep508_name(s) for s in group if isinstance(s, str))
            poetry = tool.get("poetry", {}) if isinstance(tool.get("poetry"), dict) else {}
            if isinstance(poetry.get("scripts"), dict):
                scripts.update(poetry["scripts"])
            for key in ("dependencies", "dev-dependencies"):
                if isinstance(poetry.get(key), dict):
                    deps.update(canon(k) for k in poetry[key])
            for grp in (poetry.get("group") or {}).values():
                if isinstance(grp, dict) and isinstance(grp.get("dependencies"), dict):
                    deps.update(canon(k) for k in grp["dependencies"])
            for name in ("pdm", "rye", "poe"):
                section = tool.get(name, {}) if isinstance(tool.get(name), dict) else {}
                table = section.get("tasks" if name == "poe" else "scripts")
                if isinstance(table, dict):
                    scripts.update(table)
                dev = section.get("dev-dependencies")
                for group in dev.values() if isinstance(dev, dict) else []:
                    deps.update(_pep508_name(s) for s in group if isinstance(s, str))
                if isinstance(dev, list):
                    deps.update(_pep508_name(s) for s in dev if isinstance(s, str))
            uv = tool.get("uv", {}) if isinstance(tool.get("uv"), dict) else {}
            for s in uv.get("dev-dependencies", []) or []:
                deps.add(_pep508_name(s))
            deps.update(_tool_dependencies(tool))  # e.g. [tool.hatch.metadata.hooks.*] dependencies
            hatch_cfg = tool.get("hatch")
            envs = (hatch_cfg or {}).get("envs", {}) if isinstance(hatch_cfg, dict) else {}
            for env_name, env in (envs or {}).items():
                if not isinstance(env, dict):
                    continue
                for s in env.get("dependencies", []) or []:
                    deps.add(_pep508_name(s))
                for s in env.get("extra-dependencies", []) or []:
                    deps.add(_pep508_name(s))
                for script in (env.get("scripts") or {}):
                    hatch.add(script)
                    hatch.add(env_name + ":" + script)
        for p in self.files:
            name = posixpath.basename(p).lower()
            if (name.startswith("requirements") and name.endswith((".txt", ".in"))
                    and not self.is_generated(p)):
                for line in self.read_text(p).splitlines():
                    line = line.strip()
                    if line and not line.startswith(("#", "-")):
                        deps.add(_pep508_name(line))
            elif name == "pipfile":
                data = _load_toml(self.abspath(p))
                for key in ("packages", "dev-packages"):
                    if isinstance(data.get(key), dict):
                        deps.update(canon(k) for k in data[key])
                if isinstance(data.get("scripts"), dict):
                    scripts.update(data["scripts"])
        deps.discard(None)
        return scripts, deps, hatch

    # ------------------------------------------------------------- env words
    def prefetch_env(self, names):
        self.env_files(names)

    def env_files(self, names):
        """Map each name to the repo files that mention it as a whole word.

        Real .env files are skipped (they hold secrets); .env.example and other
        templates count. Uses one `git grep` for all new names when git is there.
        """
        if self._env is None:
            self._env = {}
        todo = sorted(n for n in names if n not in self._env)
        for n in todo:
            self._env[n] = set()
        if todo:
            if not (self.git and self._env_git(todo)):
                self._env_walk(set(todo))
        return {n: self._env.get(n, set()) for n in names}

    @staticmethod
    def _secret_env(rel):
        base = posixpath.basename(rel).lower()
        return base.startswith(".env") and not base.endswith(ENV_TEMPLATE_SUFFIXES)

    def _env_git(self, names):
        for i in range(0, len(names), 400):
            args = ["grep", "-z", "-I", "-o", "-w", "-F", "--untracked", "--no-color"]
            for n in names[i:i + 400]:
                args += ["-e", n]
            try:
                res = subprocess.run(["git", "-C", self.root] + args, capture_output=True,
                                     timeout=300)
            except (OSError, subprocess.SubprocessError):
                return False
            if res.returncode not in (0, 1):
                return False
            for row in res.stdout.decode("utf-8", "replace").splitlines():
                path, _, word = row.partition("\0")
                path = path.replace("\\", "/")
                if word in self._env and not self._secret_env(path):
                    self._env[word].add(path)
        return True

    def _env_walk(self, names):
        def words(rel):
            path = self.abspath(rel)
            try:
                if os.path.getsize(path) > MAX_TEXT_BYTES:
                    return rel, ()
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                return rel, ()
            if b"\0" in data[:1024]:
                return rel, ()
            return rel, names.intersection(WORD_RE.findall(data.decode("utf-8", "ignore")))

        todo = [rel for rel in self.files
                if os.path.splitext(rel)[1].lower() not in TEXT_SKIP_EXTS
                and not self._secret_env(rel)]
        # Opening a file is slow on Windows (antivirus) and on network drives, so read in parallel.
        with ThreadPoolExecutor(max_workers=8) as pool:
            for i, (rel, hits) in enumerate(pool.map(words, todo, chunksize=64), 1):
                for word in hits:
                    self._env[word].add(rel)
                if self._progress and i % 20000 == 0:
                    self._progress(f"searching env var names: {i:,}/{len(todo):,} files")
