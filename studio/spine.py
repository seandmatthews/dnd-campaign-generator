"""Campaign spine IO — discovery and typed loaders over campaigns/<name>/.

One campaign is one directory of files (design §1); there is no database.
Everything here is read-mostly: loaders return plain dicts/lists parsed with
yaml.safe_load, missing optional files degrade to empty defaults, and an
unknown campaign raises CampaignNotFound (mapped to 404 by the server).

All runtime campaign data lives under ONE gitignored root (ticket 35):
campaign-data/, holding campaigns/, the engine home, chronicles, map
scratch and logs. The root is $CAMPAIGN_DATA_ROOT when set, else the
repo's campaign-data/ (data_root()); the campaigns root is derived from
it. CAMPAIGNS_ROOT stays a module-level constant so tests can point it at
a fixture tree (monkeypatch spine.CAMPAIGNS_ROOT); code reads it at call
time via campaigns_root(), never at import time.
"""

import re
import contextlib
import datetime
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

import yaml

STUDIO_DIR = Path(__file__).resolve().parent
REPO_ROOT = STUDIO_DIR.parent

# The campaign-data root (ticket 35): $CAMPAIGN_DATA_ROOT if set, else the
# repo's gitignored campaign-data/. data_root() reads the env at CALL time
# (the release launcher sets it before boot; tests set it per test) and
# falls back to the DATA_ROOT module global, patchable like CAMPAIGNS_ROOT.
DATA_ROOT_ENV = "CAMPAIGN_DATA_ROOT"
DATA_ROOT = REPO_ROOT / "campaign-data"


def data_root() -> Path:
    """The live campaign-data root (env first, then the module global)."""
    env = os.environ.get(DATA_ROOT_ENV, "").strip()
    return Path(env) if env else DATA_ROOT


# Derived from the data-root rule at import; patch THIS (the established
# test seam) and campaigns_root() follows. engine home / chronicle / scratch
# siblings live under data_root() too — see engines.py.
CAMPAIGNS_ROOT = data_root() / "campaigns"

# URL path segments must be plain names — no separators, no leading dots.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# New campaigns get a stricter bar: the name IS the directory name and the
# URL segment, and it should stay a lowercase slug like its siblings —
# no leading/trailing hyphens, no doubles, 1-40 chars.
_NEW_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")
# Windows reserves device names as directories in any drive path — mkdir
# would raise raw OSError, so they are refused at validation time.
_RESERVED_DEVICE_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
# lore/style files are constrained the same way (API.md, PUT style/{file})
_STYLE_RE = _NAME_RE
_CHRONICLE_RE = re.compile(r"^(\d{4})-(.+)\.md$")
_TITLE_RE = re.compile(r"^#\s*Week\s+(\d+)\s*[—–-]\s*(.+?)\s*$")


class CampaignError(Exception):
    """Raised for spine-level problems that map to 4xx responses."""


class CampaignNotFound(CampaignError):
    """Unknown campaign name — the server maps this to 404."""


class CampaignExists(CampaignError):
    """Campaign directory already exists — the server maps this to 409."""


class CampaignFileError(CampaignError):
    """A campaign file exists but does not parse (a hand-edit slipped).
    Carries the file name; the server maps this to a readable 422, never
    an opaque 500."""


# ------------------------------------------------------- per-campaign locks

# One mutation at a time per campaign: long jobs (week, world/city/site
# generation) hold the lock for their whole run, review approve/delete take
# it blocking for the length of their file rewrite. TWO layers: an
# in-process threading.Lock (threads of the server) and a cross-process
# advisory lock file under campaigns/.locks/ — the server and a separately
# launched CLI (session_notes.py, places.py) are separate processes and
# used to each believe they held the exclusive mutation.
_CAMPAIGN_LOCKS: dict = {}
_CAMPAIGN_LOCKS_GUARD = threading.Lock()

try:
    import fcntl as _fcntl
    _FILE_LOCK = "posix"
except ImportError:  # not POSIX
    try:
        import msvcrt as _msvcrt
        _FILE_LOCK = "windows"
    except ImportError:  # exotic platform: process-local exclusion only
        _FILE_LOCK = "none"

_log = logging.getLogger(__name__)


def _locks_root() -> Path:
    """The shared lock-file directory: OUTSIDE every campaign dir (a lock
    file inside campaigns/<name>/ would dirty the per-campaign git history
    the snapshot trail relies on)."""
    return campaigns_root() / ".locks"


class CampaignLock:
    """One campaign's mutation lock: threading.Lock + a cross-process
    byte-range lock file. Same surface as threading.Lock had here:
    `acquire(blocking=..., timeout=...)` -> bool, `release()`, and the
    `with` form blocks. OS releases the file lock if a process dies, so a
    killed CLI cannot wedge the server. NEVER nest acquisitions of the
    same campaign's lock — a Lock is not reentrant, and a blocking
    re-acquire deadlocks."""

    def __init__(self, name: str):
        self._name = str(name)
        self._local = threading.Lock()
        self._fh = None
        self._no_file_lock = False

    def _lock_file(self) -> Path:
        # computed at ACQUIRE time: campaigns_root() is patched per test,
        # and a path cached at first use would outlive its tmp root
        return _locks_root() / (
            re.sub(r"[^A-Za-z0-9._-]", "_", self._name) + ".lock")

    def acquire(self, blocking: bool = True, timeout: float = -1.0) -> bool:
        deadline = None
        if blocking:
            if not self._local.acquire(True, timeout):
                return False
            if timeout is not None and timeout >= 0:
                deadline = time.monotonic() + timeout
        else:
            if not self._local.acquire(False):
                return False
        try:
            if not self._acquire_file(blocking, deadline):
                self._local.release()
                return False
        except BaseException:
            self._local.release()
            raise
        return True

    def _acquire_file(self, blocking: bool, deadline) -> bool:
        if _FILE_LOCK == "none" or self._no_file_lock:
            return True
        lock_path = self._lock_file()
        # ONE open, inside the degrade handler (ticket 13): a transient
        # OSError on ANY open — the bootstrap, or a Windows sharing
        # violation on the lock file itself — degrades loudly to
        # process-local locking instead of escaping as a 500. The handle
        # is held for the lock's lifetime (retries lock and unlock THE
        # SAME handle; reopening per attempt only churned handles).
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            gitignore = lock_path.parent / ".gitignore"
            if not gitignore.exists():
                # lock files are runtime state: keep them out of the
                # campaigns repo's git status
                gitignore.write_text("*\n", encoding="utf-8")
            fh = open(lock_path, "a+b")  # noqa: SIM115 — the handle is held
            # for the lock's lifetime and closed by release()
        except OSError as e:
            # an unwritable locks root (read-only checkout?) must not block
            # every mutation: degrade to process-local exclusion, loudly once
            self._no_file_lock = True
            _log.warning("campaign lock file %s unusable (%s) — falling "
                         "back to process-local locking only", lock_path, e)
            return True
        while True:
            try:
                if _FILE_LOCK == "windows":
                    fh.seek(0)
                    _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
                else:
                    _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                self._fh = fh
                return True
            except OSError:
                if not blocking or (deadline is not None
                                    and time.monotonic() >= deadline):
                    fh.close()
                    return False
                time.sleep(0.05)

    def locked(self) -> bool:
        """Whether the campaign lock is held RIGHT NOW — by this process
        OR an external one (a CLI mid-write). The queue asks at submit
        time to answer "will this job start immediately?" honestly
        (routers/common.submit_campaign_job); the file-lock probe makes
        that answer true across processes too (ticket 13 — an in-process
        -only probe reported 'not queued' while a CLI held the file
        lock). Still a probe reading, not a promise: the holder may be a
        CLI about to release, and a degraded (no file lock) CampaignLock
        can only see its own process."""
        if self._local.acquire(False):
            self._local.release()
        else:
            return True
        if _FILE_LOCK == "none" or self._no_file_lock:
            return False
        lock_path = self._lock_file()
        try:
            fh = open(lock_path, "a+b")  # noqa: SIM115 — probe: closed
            # within this function
        except OSError:
            return False  # unreadable lock area: an honest "unknown" is
            # not available; the in-process answer is all we have
        try:
            if _FILE_LOCK == "windows":
                fh.seek(0)
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
                _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
            else:
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
            return False
        except OSError:
            return True  # contended: someone (this process raced, or an
            # external one) holds it — "held" is the honest answer
        finally:
            fh.close()

    def release(self) -> None:
        try:
            fh, self._fh = self._fh, None
            if fh is not None:
                try:
                    if _FILE_LOCK == "windows":
                        fh.seek(0)
                        _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
                    else:
                        _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
                finally:
                    fh.close()
        finally:
            self._local.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def campaign_lock(name: str) -> CampaignLock:
    """The mutation lock for one campaign (created on first use).

    A CampaignLock IS a context manager, so `with spine.campaign_lock(n):`
    is the blocking form — used by review approve/delete (short file
    rewrites; they wait for a running job to finish) and the CLIs. Long
    campaign jobs QUEUE (routers/common.submit_campaign_job): the queue's
    before_run hook takes the lock via jobs.take_turn while the job
    record is still 'queued' (position visible, cancellable) and holds it
    for the job's whole run — so a long job never 409s at submit any
    more; the fast 409s belong to the short interactive writers
    (campaign_lock_nowait / spine.lock_nowait). The lock is honored
    across processes (server vs CLI) through the lock file; NEVER nest
    acquisitions of the same campaign's lock. The name is canonicalized
    (canonical_name, ticket 56) so a request under a case or trailing-dot
    alias shares the real campaign's lock instead of minting a second
    one beside it."""
    name = canonical_name(name)
    with _CAMPAIGN_LOCKS_GUARD:
        return _CAMPAIGN_LOCKS.setdefault(name, CampaignLock(name))


class CampaignBusy(CampaignError):
    """Another writer holds the campaign lock — retry when it finishes."""


def lock_nowait(name: str, action: str, timeout: float = 0.0):
    """The ONE fast-fail interactive-writer acquire: the campaign lock,
    never a blocking wait (a week job can hold the lock for half an hour),
    raising CampaignBusy carrying the standard retry message. timeout=0 is
    the instant fail the routes answer 409 with; the session store passes a
    1s grace to absorb micro-holds. Sessions, the routers, and
    proposal_flows each map CampaignBusy to their own error type with the
    SAME words — one implementation of the dance, three one-line mappers."""

    @contextlib.contextmanager
    def cm():
        lock = campaign_lock(name)
        # threading semantics: blocking=False ignores timeout, so pick the
        # form explicitly — instant check, or a bounded grace wait
        ok = lock.acquire(True, timeout) if timeout > 0 \
            else lock.acquire(False)
        if not ok:
            raise CampaignBusy(
                f"{action} cannot run right now: another job is "
                "already modifying this campaign (a week run?) — retry "
                "when it finishes. Nothing was changed.")
        try:
            yield
        finally:
            lock.release()
    return cm()


def try_file_lock(path: Path):
    """The cross-process half of CampaignLock as a standalone primitive: a
    non-blocking byte-range lock over one file (the same Windows msvcrt /
    POSIX flock dance CampaignLock performs), for shared resources that are
    not campaigns — the engine's home (engines.py, ticket 26). Returns
    (handle, held): handle is the locked open file (release it with
    release_file_lock) unless another process holds the lock (held=True),
    or the platform / filesystem offers no file locking, in which case both
    are falsy — callers degrade to in-process exclusion exactly like a
    CampaignLock whose lock file is unusable. Never blocks."""
    if _FILE_LOCK == "none":
        return None, False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+b")  # noqa: SIM115 — the caller holds the handle
    except OSError as e:
        _log.warning("file lock %s unusable (%s) — no cross-process "
                     "exclusion over it", path, e)
        return None, False
    try:
        if _FILE_LOCK == "windows":
            fh.seek(0)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
        else:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None, True
    return fh, False


def release_file_lock(fh) -> None:
    """Release a handle try_file_lock returned (a no-op on None)."""
    if fh is None:
        return
    try:
        if _FILE_LOCK == "windows":
            fh.seek(0)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
        else:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
    finally:
        fh.close()


def file_lock_held(path: Path) -> bool:
    """Whether ANY process holds the file lock — the fresh-handle probe
    CampaignLock.locked uses (locking through a second handle fails while a
    first one holds the byte range, including a holder inside this very
    process). Platforms without file locking have no cross-process answer:
    False is the only honest in-process one."""
    if _FILE_LOCK == "none":
        return False
    try:
        fh = open(path, "a+b")  # noqa: SIM115 — probe: closed within this
        # function
    except OSError:
        return False
    try:
        if _FILE_LOCK == "windows":
            fh.seek(0)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_NBLCK, 1)
            _msvcrt.locking(fh.fileno(), _msvcrt.LK_UNLCK, 1)
        else:
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            _fcntl.flock(fh.fileno(), _fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def campaigns_root() -> Path:
    """The live campaigns root (reads the module global so tests can patch)."""
    return CAMPAIGNS_ROOT


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _safe_load(path: Path):
    if not path.exists():
        return None
    # A brief Windows sharing violation while a writer replaces the file
    # (the write side already retries) is worth two reads — but a
    # persistently unreadable file must NOT degrade to "empty": loaders
    # would render a missing campaign instead of naming the problem.
    last_err = None
    for attempt in range(3):
        try:
            with open(path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except OSError as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            first = str(e).splitlines()[0] if str(e) else type(e).__name__
            raise CampaignFileError(
                f"{path.name} does not parse ({first}) — a hand-edit slipped? "
                f"Fix or delete the file, or restore it with git checkout -- {path}")
    raise CampaignFileError(
        f"{path.name} cannot be read ({last_err}) — a concurrent writer "
        "or a permissions problem; retrying the request may clear it")


def read_yaml_dict(path: Path) -> dict | None:
    """Tolerant YAML-dict read for manifest-style files: safe_load a UTF-8
    file and return it only when it parses to a mapping. A missing file, an
    OSError, YAML that does not parse, or a non-mapping document all read
    as None — the caller decides what absence means."""
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return doc if isinstance(doc, dict) else None


def _replace_with_retry(tmp: Path, path: Path, attempts: int = 3) -> None:
    """os.replace, but tolerant of a Windows sharing violation: a reader
    holding the destination open without FILE_SHARE_DELETE makes the replace
    raise PermissionError for an instant. Three short tries covers it; a
    genuine failure still raises."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


def write_text_atomic(path: Path, text: str) -> Path:
    """Write via tmp + os.replace so a crash or concurrent reader never sees
    a half-written spine file. Same volume, so the replace is atomic; the
    tmp name is UNIQUE (mkstemp) — a fixed `<name>.tmp` let two writers
    under the same campaign lock clobber each other's partial write.
    newline="" writes the text EXACTLY as given: default text mode would
    re-translate every \\n and turn CRLF-carrying input (a Windows
    state.yaml, a hand-edited style file) into \\r\\r\\n on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".",
                                    suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        _replace_with_retry(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)  # no litter, destination untouched
        raise
    return path


def dump_yaml(doc) -> str:
    """THE serialization for every canon/spine file (sort_keys=False,
    allow_unicode=True, width=100). One place on purpose: publication's
    revision checks hash the exact bytes these calls produce, so writers
    must not drift apart (timeline.dump's docstring is the contract's
    canary)."""
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)


def valid_name(name: str) -> bool:
    return bool(name) and bool(_NAME_RE.match(name))


def canonical_name(name: str) -> str:
    """The directory's REAL name for a requested campaign name (ticket 56).

    _NAME_RE admits mixed case and trailing dots for EXISTING campaigns,
    and on NTFS 'foo.' / 'Ember-Coast' address the same directory as
    'foo' / 'ember-coast' — but the lock file name, git pathspecs, and
    error messages all keyed on the REQUESTED spelling, so a request
    under 'foo.' held foo..lock while every writer on 'foo' held
    foo.lock: same canon, different locks, mutual exclusion silently
    bypassed (and `git add -A -- <alias>` staged nothing, so alias
    writes went un-snapshotted). Resolve the request against the
    campaigns root: an exact match is itself; a case-insensitive or
    trailing-dot match (the dots strip from the REQUEST only, mirroring
    Win32 path rules — a POSIX directory genuinely named 'foo.' stays a
    distinct campaign) returns the DIRECTORY's name, so one campaign has
    one lock file and one git pathspec; anything else returns the
    request unchanged for the caller's not-found path to refuse."""
    if not valid_name(name):
        return name
    root = campaigns_root()
    try:
        dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      key=lambda d: d.name)
    except OSError:
        return name
    if any(d.name == name for d in dirs):
        return name
    want = name.rstrip(".").lower()
    for d in dirs:
        if d.name.lower() == want:
            return d.name
    return name


def campaign_dir(name: str) -> Path:
    if not valid_name(name):
        raise CampaignNotFound(f"invalid campaign name: {name!r}")
    # identity canonicalized at the boundary (ticket 56): the directory's
    # real name is what flows to every lock, write, and pathspec
    return campaigns_root() / canonical_name(name)


def require_campaign(name: str) -> Path:
    d = campaign_dir(name)
    if not (d / "campaign.yaml").is_file():
        raise CampaignNotFound(f"unknown campaign: {name}")
    return d


# ----------------------------------------------------------------- creation

_CREATE_ENGINE_POINTERS = {
    "mapping": "../dnd-mapping",
    "simulator": "../agentic-simulator",
    "verification": "../verification",
}


def create_campaign(name: str, title: str = "") -> dict:
    """A brand-new empty campaign: directory, campaign.yaml, section dirs.

    The name becomes the directory name and URL segment, so it is stricter
    than existing-campaign lookup: a lowercase slug, 1-40 chars. This is the
    one spine writer that starts from nothing — everything else fills in
    sections of an existing campaign, and the first feed is POST /world's
    seed (generation is fed or refused; there is nothing to refuse yet)."""
    if not _NEW_NAME_RE.match(name or ""):
        raise CampaignError(
            "campaign name must be a lowercase slug — a-z, 0-9, hyphens, "
            "1-40 chars, starting with a letter or digit (e.g. ember-coast)")
    if name in _RESERVED_DEVICE_NAMES:
        raise CampaignError(f"campaign name {name!r} is reserved on Windows")
    d = campaigns_root() / name
    if d.exists():
        raise CampaignExists(f"campaign already exists: {name}")
    title = (title or "").strip() or name
    try:
        (d / "world").mkdir(parents=True)
    except FileExistsError:
        # two concurrent creates: the exists() check above raced — map to the
        # same typed error instead of a raw OSError 500
        raise CampaignExists(f"campaign already exists: {name}")
    (d / "lore" / "style").mkdir(parents=True)
    (d / "simulation" / "chronicle").mkdir(parents=True)
    (d / "maps" / "cities").mkdir(parents=True)
    (d / "maps" / "sites").mkdir(parents=True)
    meta = {
        "name": name,
        "title": title,
        "created": datetime.date.today().isoformat(),
        "engines": dict(_CREATE_ENGINE_POINTERS),
    }
    write_text_atomic(d / "campaign.yaml",
                      yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
    return {"name": name, "title": title, "created": meta["created"],
            "has": _has_sections(d)}


# ---------------------------------------------------------------- discovery


def list_campaigns() -> list:
    root = campaigns_root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not (d / "campaign.yaml").is_file():
            continue
        # A folder whose name fails _NAME_RE can never be addressed by the
        # API (campaign_dir refuses it), so listing it would hand the UI an
        # option every route 404s on — skip it; the doctor names the skips.
        if not valid_name(d.name):
            continue
        try:
            meta = _safe_load(d / "campaign.yaml") or {}
        except CampaignFileError:
            # one corrupt campaign.yaml must not 4xx the whole listing —
            # degrade to folder-name identity; the file routes still 422
            # with the file name when that campaign is opened
            meta = {}
        # the DIRECTORY name is the app's address, always (ticket 56): a
        # hand-edited campaign.yaml `name:` that is merely VALID used to
        # become the listed address (Ember-Coast for folder ember-coast) —
        # files and lock coincide on NTFS, but `git add -A -- <alias>`
        # silently stages nothing and the campaign's undo trail died,
        # reported only as snapshot 'failed'. The title still reads the
        # file; identity never diverges from the folder.
        out.append({
            "name": d.name,
            "title": meta.get("title") or d.name,
            "created": str(meta.get("created") or ""),
            "has": _has_sections(d),
        })
    return out


def _has_sections(d: Path) -> dict:
    return {
        "world": (d / "world" / "geography.yaml").is_file()
                 or world_source(d.name) is not None,
        "lore": (d / "lore" / "timeline.yaml").is_file(),
        "simulation": (d / "simulation" / "state.yaml").is_file(),
        "maps": bool(_map_slugs(d, "cities") or _map_slugs(d, "sites")),
    }


def skipped_campaign_folders() -> list:
    """Folders under campaigns/ that look like campaigns (campaign.yaml
    inside) but whose names cannot be addressed — the doctor reports them
    so a rename is the obvious fix instead of a silent absence."""
    root = campaigns_root()
    if not root.is_dir():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "campaign.yaml").is_file()
        and not valid_name(d.name)
    )


# ------------------------------------------------------------------ loaders


def load_campaign_yaml(name: str) -> dict:
    return _safe_load(require_campaign(name) / "campaign.yaml") or {}


def campaign_system(ydoc) -> "str | None":
    """The campaign's `system:` field, normalized: `value.strip().lower()`
    for a plain string; None for missing, empty, or a non-string scalar
    (YAML parses `system: 5` as an int). The ONE sanctioned accessor in
    core — the companion repo's pack installer re-implements these exact
    semantics (no shared code is allowed either direction), so neither side
    may drift (ticket 48). No enum, no catalog: the core does not know
    systems, and must not grow one."""
    if not isinstance(ydoc, dict):
        return None
    value = ydoc.get("system")
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def load_geography(name: str) -> dict:
    geo = _safe_load(campaign_dir(name) / "world" / "geography.yaml")
    return geo or {}


def world_source(name: str):
    """The FMG dump under world/ (source.map / source.json / any source.*)."""
    world = campaign_dir(name) / "world"
    if not world.is_dir():
        return None
    for candidate in sorted(world.glob("source.*")):
        return candidate
    return None


def load_timeline(name: str) -> list:
    doc = _safe_load(campaign_dir(name) / "lore" / "timeline.yaml")
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        return doc.get("entries") or []
    return []


def load_entities(name: str) -> list:
    """The campaign's entities as a canonical list of dicts
    (id?/kind/name/aliases/note, plus `file` for page-backed entities).

    Entity pages are canonical (obsidian-integration.md §1): one markdown
    file per entity in lore/entities/. A legacy lore/entities.yaml that has
    not been migrated yet still reads (boot migrates it away), so a campaign
    zipped off an old install degrades gracefully instead of vanishing."""
    pages = entity_pages(name)
    if pages:
        return [{k: p[k] for k in p if k != "body"} for p in pages]
    return _legacy_entities(name)


# ------------------------------------------------------- entity pages (vault)

# Frontmatter fence: --- at the very top, closing --- on its own line. A
# leading UTF-8 BOM (Windows Notepad's "UTF-8 with BOM") is tolerated:
# without it a BOM'd page parsed as frontmatter-less and silently lost its
# kind (P3-19) — readers that care flag the BOM as a finding instead.
_FRONTMATTER_RE = re.compile(r"\A\ufeff?---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)
# A page filename is a display name (spaces, dots fine) with the
# filesystem's trouble characters already excluded; .md rides in the caller.
_PAGE_STEM_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,80}$")


def valid_page_name(page: str) -> bool:
    """A lore/entities page stem as it may appear in a URL path segment —
    a display name, but no separators, control chars, leading dot, or
    traversal."""
    return bool(page) and ".." not in page and bool(_PAGE_STEM_RE.match(page))


def entity_page_filename(name: str) -> str:
    """The page filename for an entity name — display names, per the vault
    design's recommendation (typing [[House Fer autocompletes in Obsidian;
    nothing here becomes a shell path). Filesystem-hostile characters fold
    to spaces; a hostile stem (reserved device name, trailing dot/space)
    raises CampaignError — mint a different name instead."""
    stem = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', " ", name or "")
    stem = re.sub(r"\s+", " ", stem).strip()
    if (not _PAGE_STEM_RE.match(stem) or stem.startswith(".")
            or windows_hostile_stem(stem)):
        raise CampaignError(f"cannot make an entity page filename from {name!r}")
    return stem + ".md"


def parse_entity_page(path: Path) -> dict:
    """One lore/entities/*.md page -> the entity dict (id?/kind/name/
    aliases/note/body/file).

    Frontmatter carries today's exact fields; the body's first paragraph is
    the note (what entities.yaml used to carry), the rest is free lore —
    sections, wiki-links. A page with no frontmatter degrades to its
    filename stem (a hand-created file is a page, not an error); frontmatter
    that does not parse is a hand-edit slip -> CampaignFileError (readable
    422, never an opaque 500)."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise CampaignFileError(
            f"{path.name} is not UTF-8 — fix the encoding, or restore it "
            f"with git checkout -- lore/entities/{path.name}")
    m = _FRONTMATTER_RE.match(text)
    body = text[m.end():].lstrip("\n") if m else text
    meta = {}
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError as e:
            first = str(e).splitlines()[0] if str(e) else type(e).__name__
            raise CampaignFileError(
                f"{path.name} does not parse ({first}) — a hand-edit slipped? "
                f"Fix the frontmatter, or restore it with "
                f"git checkout -- lore/entities/{path.name}")
        if not isinstance(meta, dict):
            raise CampaignFileError(
                f"{path.name}: frontmatter must be a mapping of fields")
    aliases = meta.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]

    def _id_list(key):
        """A frontmatter list of place ids (resides_at/present_at). A scalar
        degrades to a one-item list — the place-view plan bans ambiguous
        scalar/list formats for NEW writes, but a hand edit may use either;
        readers accept both."""
        v = meta.get(key)
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in v if str(x).strip()] \
            if isinstance(v, list) else []

    geo_ref = meta.get("geography_ref")
    page = {
        "id": str(meta.get("id") or "").strip(),
        "kind": str(meta.get("kind") or "").strip(),
        "name": str(meta.get("name") or "").strip() or path.stem,
        "aliases": [str(a) for a in aliases],
        "note": _first_paragraph(body),
        "body": body.strip(),
        "file": f"lore/entities/{path.name}",
    }
    # place-view identity fields (the place-centered view): present
    # on place pages, absent (None/[]) everywhere else. The full raw
    # frontmatter is NOT carried — PATCH round-trips preserve unknown keys
    # via the places module's own raw reader instead.
    page["place_id"] = str(meta.get("place_id") or "").strip()
    page["parent_place_id"] = str(meta.get("parent_place_id") or "").strip()
    page["resides_at"] = _id_list("resides_at")
    page["present_at"] = _id_list("present_at")
    page["geography_ref"] = geo_ref if isinstance(geo_ref, dict) else None
    page["preferred_map"] = str(meta.get("preferred_map") or "").strip()
    return page


def _first_paragraph(body: str) -> str:
    """The note: the first paragraph, skipping leading headings."""
    lines, para, started = body.splitlines(), [], False
    for line in lines:
        t = line.strip()
        if not t:
            if started:
                break
            continue
        if t.startswith("#"):
            if started:
                break
            continue  # a leading heading is a title, not the note
        started = True
        para.append(t)
    return " ".join(para).strip()


def entity_pages(name: str) -> list:
    """Every entity page, sorted by filename. Degrades to [] when the
    campaign has none; a page that does not parse raises CampaignFileError
    (the caller maps it to a readable 422)."""
    d = campaign_dir(name) / "lore" / "entities"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        try:
            out.append(parse_entity_page(p))
        except FileNotFoundError:
            continue  # unlinked mid-glob (migration/race) — never a 500
    return out


def entity_page_text(page_name: str, frontmatter: dict, body: str) -> str:
    """The exact bytes an entity page write produces — shared by
    write_entity_page and publication staging, so a staged after-image is
    byte-identical to what a direct write would have made."""
    meta = {k: v for k, v in frontmatter.items()
            if v not in (None, "", [], {})}
    text = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True,
                          default_flow_style=False).strip()
    return f"---\n{text}\n---\n\n{body.strip()}\n" if text else f"{body.strip()}\n"


_STATBLOCK_FENCE_RE = re.compile(
    r"(?ms)^[ \t]*```statblock[^\n]*\n.*?^[ \t]*```[ \t]*\r?$")


def apply_statblock_fence(page_text: str, fence: str) -> str:
    """Insert — or replace — a ```statblock fence in an entity page's text,
    byte-surgically (ticket 49). Every byte OUTSIDE the fence region is
    returned unchanged: frontmatter, prose, unknown keys, comments, and
    line endings all survive (an entity_page_text round-trip would drop
    empty-valued frontmatter keys and re-dump the YAML — the ticket-10 bug
    class). The fence lands at the end of the body, after one blank line;
    a previous fence (the LAST one — appends live at the end) is replaced
    in place, so re-approve updates and never duplicates. The inserted
    text takes the page's dominant newline (by count, not mere presence —
    one pasted CRLF line must not flip an LF page). `page_text` must be
    the file decoded as-is (no newline translation): publication writes
    text bytes verbatim, so CRLF stays CRLF."""
    fence = (fence or "").replace("\r\n", "\n").replace("\r", "\n")
    fence = fence.strip("\n")
    if not fence.startswith("```statblock"):
        raise CampaignError(
            "a statblock fence must start with a ```statblock line")
    nl = ("\r\n" if page_text.count("\r\n")
          > page_text.count("\n") - page_text.count("\r\n") else "\n")
    block = nl.join(fence.split("\n")) + nl
    last = None
    for last in _STATBLOCK_FENCE_RE.finditer(page_text):
        pass  # scan to the LAST fence; earlier ones stay history
    if last is not None:
        return page_text[:last.start()] + block[:-len(nl)] \
            + page_text[last.end():]
    if not page_text.endswith(nl):
        page_text += nl
    if not page_text.endswith(nl * 2):
        page_text += nl
    return page_text + block


def write_entity_page(name: str, page_name: str, frontmatter: dict,
                      body: str) -> Path:
    """Create (never overwrite) one entity page. Create-only is the point:
    a re-run migration or a re-stub must never clobber hand edits."""
    require_campaign(name)
    filename = entity_page_filename(page_name)
    path = campaign_dir(name) / "lore" / "entities" / filename
    if path.exists():
        raise CampaignExists(f"entity page already exists: {filename}")
    return write_text_atomic(
        path, entity_page_text(page_name, frontmatter, body))


def _legacy_entities(name: str) -> list:
    """Normalize every entities.yaml shape seen in the wild into the
    canonical list: {entities: [...]}, a bare list, or groups keyed by kind
    (the fixture's people/factions/places — the key IS the kind; a singular
    `alias:` field rides along as a one-item aliases list)."""
    doc = _safe_load(campaign_dir(name) / "lore" / "entities.yaml")
    if isinstance(doc, dict):
        entities = doc.get("entities")
        if isinstance(entities, list):
            return [_legacy_entity(e) for e in entities
                    if isinstance(e, dict)]
        out = []  # grouped shape: {<kind>: [{name, note, alias?...}]}
        for kind, items in doc.items():
            if isinstance(items, list):
                out.extend(_legacy_entity(e, kind=str(kind))
                           for e in items if isinstance(e, dict))
        return out
    if isinstance(doc, list):
        return [_legacy_entity(e) for e in doc if isinstance(e, dict)]
    return []


def _legacy_entity(e: dict, kind: str = "") -> dict:
    aliases = e.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if e.get("alias") and e["alias"] not in aliases:
        aliases = [str(e["alias"])] + list(aliases)
    return {
        "id": str(e.get("id") or "").strip(),
        "kind": str(e.get("kind") or kind or "").strip(),
        "name": str(e.get("name") or "").strip(),
        "aliases": [str(a) for a in aliases],
        "note": str(e.get("note") or "").strip(),
    }


def migrate_entities_yaml_to_pages(name: str):
    """One-time entities.yaml -> lore/entities/*.md (vault §1, stage 1).

    Migrate-and-delete, on the same pass: pages are written for entities
    whose page does not exist yet (a partial earlier migration never
    clobbers hand edits — filename collisions get a ' (2)' suffix), then
    entities.yaml is deleted. Returns the number of pages written, 0 when
    the yaml existed but was empty, None when there was nothing to migrate.
    The caller holds the campaign lock and takes the canon snapshot."""
    require_campaign(name)
    yaml_path = campaign_dir(name) / "lore" / "entities.yaml"
    if not yaml_path.is_file():
        return None
    entities = _legacy_entities(name)
    pages_dir = campaign_dir(name) / "lore" / "entities"
    # casefold: Windows filenames are case-insensitive, so "The Reliquary"
    # and "the Reliquary" are the same file — the second needs the suffix
    used = {p.name.casefold()
            for p in pages_dir.glob("*.md")} if pages_dir.is_dir() else set()
    written = 0
    for e in entities:
        if not e["name"]:
            continue
        try:
            filename = entity_page_filename(e["name"])
        except CampaignError:
            filename = slugify(e["name"]) + ".md"  # hostile stem: slug it
        while filename.casefold() in used:
            filename = f"{filename[:-3]} (2).md"
        used.add(filename.casefold())
        meta = {"id": e["id"], "kind": e["kind"], "name": e["name"],
                "aliases": e["aliases"]}
        write_entity_page(name, filename[:-3], meta, e["note"])
        written += 1
    yaml_path.unlink()
    return written


def style_files(name: str) -> list:
    d = campaign_dir(name) / "lore" / "style"
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


def read_style(name: str, filename: str) -> str:
    path = _style_path(name, filename)
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # same contract as read_chronicle: latin-1 never fails, and one
        # resaved file must not 500 the whole Lore tab (get_lore reads
        # every style file)
        return path.read_text(encoding="latin-1")


def write_style(name: str, filename: str, text: str) -> Path:
    path = _style_path(name, filename, must_exist=True)
    return write_text_atomic(path, text)


def create_style(name: str, filename: str, text: str) -> Path:
    """Create (never overwrite) a lore/style file — the staging path for the
    seed lists the fed-or-refused guardrail demands (name helper)."""
    path = _style_path(name, filename)
    if path.exists():
        raise CampaignExists(f"style file already exists: {filename}")
    return write_text_atomic(path, text)


def windows_hostile_stem(stem: str) -> str | None:
    """Why a would-be directory/file stem cannot exist on Windows, or None
    when it is fine: reserved device names, trailing dot/space (Windows
    silently strips them, so the path written is not the path asked for).
    Callers turn this into a readable 422 instead of a raw OSError."""
    if stem.lower() in _RESERVED_DEVICE_NAMES:
        return f"'{stem}' is a reserved Windows device name"
    if stem != stem.rstrip(" ."):
        return f"'{stem}' ends with a dot or space — Windows strips it"
    return None


def _style_path(name: str, filename: str, must_exist: bool = False) -> Path:
    if not _STYLE_RE.match(filename or ""):
        raise CampaignError(f"invalid style filename: {filename!r}")
    hostile = windows_hostile_stem(Path(filename).stem)
    if hostile:
        raise CampaignError(f"invalid style filename: {hostile}")
    path = campaign_dir(name) / "lore" / "style" / filename
    if must_exist and not path.is_file():
        raise CampaignNotFound(f"no such style file: {filename}")
    return path


def load_state(name: str) -> dict:
    doc = _safe_load(campaign_dir(name) / "simulation" / "state.yaml")
    return doc if isinstance(doc, dict) else {}


def state_clock_week(doc: dict):
    """meta.clock.week of a state document (None when absent) — the one
    clock reader, shared by the session store and the simulation router."""
    return ((doc.get("meta") or {}).get("clock") or {}).get("week")


# ------------------------------------------------- engine vocabulary mirror
#
# The engine's resource ladder and set_field allowlist, mirrored here as
# the studio's ONE copy (sessions.py and the simulation router import from
# here; studio/tests/test_engine_parity.py asserts the mirror matches the
# engine's own declarations). Never edit one side without the other.

RESOURCE_KEYS = ("coin", "muscle", "legitimacy")
RESOURCE_LEVELS = ("none", "low", "medium", "high")
FIELD_KEYS = ("goal", "kind", "secret", "species")


def validate_faction_docs(factions: list) -> list[dict]:
    """The state.yaml factions list from the create form: ids derived from
    names (underscore slugs — tick.py's HOLDS log lines parse [a-z_]+),
    resources filled to the canonical three with "low" defaults. Raises
    CampaignError (the router maps it to 422) — the studio's only pre-flight
    state-schema validator, so it lives with the schema, not the route."""
    if not (2 <= len(factions) <= 5):
        raise CampaignError(
            f"a sandbox needs 2-5 factions that want things from each "
            f"other (got {len(factions)})")
    seen_ids: dict[str, str] = {}
    out = []
    for f in factions:
        name = str(f.get("name") or "").strip()
        goal = str(f.get("goal") or "").strip()
        if not name:
            raise CampaignError("every faction needs a name")
        if not goal:
            raise CampaignError(
                f"faction '{name}' needs a goal — what it wants, in "
                "one line (that want is what drives the week)")
        fid = slugify(name).replace("-", "_")
        if not fid:
            raise CampaignError(
                f"faction name '{name}' has no usable id characters")
        if fid == "world":
            raise CampaignError(
                "faction id 'world' is reserved (world events are "
                "scenery, not a faction)")
        if fid in seen_ids:
            raise CampaignError(
                f"two factions collapse to the same id '{fid}' "
                f"('{name}' and '{seen_ids[fid]}') — give them "
                "distinct names")
        seen_ids[fid] = name
        resources = {}
        raw_resources = f.get("resources")
        if raw_resources is None:
            raw_resources = {}
        if not isinstance(raw_resources, dict):
            # a scalar/list resources is a schema violation, not a crash:
            # CampaignError maps to 4xx on every route (ticket 21)
            raise CampaignError(
                f"faction '{name}': resources must be a mapping of "
                "resource to level (coin, muscle, legitimacy), not a "
                f"{type(raw_resources).__name__}")
        for key, level in raw_resources.items():
            if key not in RESOURCE_KEYS:
                raise CampaignError(
                    f"faction '{name}': unknown resource '{key}' "
                    f"(use coin, muscle, legitimacy)")
            level = str(level).lower().strip()
            if level not in RESOURCE_LEVELS:
                raise CampaignError(
                    f"faction '{name}': resource '{key}' must be one "
                    f"of {', '.join(RESOURCE_LEVELS)}")
            resources[key] = level
        for key in RESOURCE_KEYS:
            resources.setdefault(key, "low")
        out.append({
            "id": fid,
            "name": name,
            "goal": goal,
            "resources": resources,
        })
        if str(f.get("kind") or "").strip():
            out[-1]["kind"] = str(f["kind"]).strip()
        if str(f.get("place") or "").strip():
            out[-1]["place"] = str(f["place"]).strip()
    return out


def write_state(name: str, doc: dict) -> Path:
    """Write simulation/state.yaml from a dict (the create-a-simulation
    flow). The caller validates content; this is the typed writer."""
    require_campaign(name)
    path = campaign_dir(name) / "simulation" / "state.yaml"
    return write_text_atomic(path, dump_yaml(doc))


def place_id(name: str) -> str:
    """A stable place id from its name — EXACTLY the engine's derivation
    (store._place_id: word-slug, 80-char cap, "place" fallback), mirrored
    here rather than imported because the studio drives the engine through
    its CLI, never by importing across projects. The parity test in
    studio/tests pins the two implementations together."""
    slug = "-".join(re.findall(r"[a-z0-9]+", str(name).lower()))[:80] \
        .rstrip("-")
    return slug or "place"


def state_places(state: dict) -> list:
    """Every simulated place, normalized for display — behaviorally
    IDENTICAL to the engine's store.places_of (the parity test pins it):
    the multi-place `places` list when the state carries one, else the
    legacy single city as a one-entry list. Ids derive the engine's way
    (place_id) so faction.place references line up on both sides of the
    seam: same cap, same fallback, same forced id for the legacy city."""
    out = []
    raw = state.get("places") if isinstance(state, dict) else None
    if isinstance(raw, list) and raw:
        entries = [p for p in raw if isinstance(p, dict)]
    else:
        city = state.get("city") if isinstance(state, dict) else None
        entries = [city] if isinstance(city, dict) else []
    legacy = not (isinstance(raw, list) and raw)
    for p in entries:
        name = str(p.get("name") or "").strip()
        if not name:
            continue
        place = dict(p)
        place["name"] = name
        if legacy:
            place["id"] = place_id(name)  # the engine forces it for cities
            place.setdefault("kind", "city")
        else:
            place.setdefault("id", place_id(name))
            place.setdefault("kind", "place")
        out.append(place)
    return out


def chronicles(name: str) -> list:
    """[{"week","title","file"}] parsed from simulation/chronicle/*.md."""
    d = campaign_dir(name) / "simulation" / "chronicle"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        m = _CHRONICLE_RE.match(p.name)
        if not m:
            continue
        week = int(m.group(1))
        title = m.group(2).replace("-", " ")
        # one cp1252-resaved file must degrade, not 500 the Simulation tab
        try:
            first = p.read_text(encoding="utf-8").splitlines()[:1]
        except UnicodeDecodeError:
            out.append({"week": week, "title": "(unreadable — not UTF-8)",
                        "file": f"simulation/chronicle/{p.name}"})
            continue
        if first:
            t = _TITLE_RE.match(first[0])
            if t:
                title = t.group(2)
        out.append({"week": week, "title": title,
                    "file": f"simulation/chronicle/{p.name}"})
    return sorted(out, key=lambda c: c["week"])


def read_chronicle(name: str, week: int):
    for c in chronicles(name):
        if c["week"] == week:
            path = campaign_dir(name) / c["file"]
            c = dict(c)
            try:
                c["markdown"] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # same contract as chronicles(): latin-1 never fails, and
                # replacement chars beat an opaque 500 on a resaved file
                c["markdown"] = path.read_text(encoding="latin-1")
            return c
    return None


def count_pending(name: str) -> int:
    """Pending review files: lore/pending-*.yaml + simulation/pending-week-*.yaml."""
    n = 0
    for pattern in ("lore/pending-*.yaml", "simulation/pending-week-*.yaml"):
        n += len(list(campaign_dir(name).glob(pattern)))
    return n


def _map_slugs(d: Path, kind: str) -> list:
    base = d / "maps" / kind
    if not base.is_dir():
        return []
    return sorted(p.name for p in base.iterdir() if p.is_dir())


def map_slugs(name: str, kind: str) -> list:
    return _map_slugs(campaign_dir(name), kind)


# ------------------------------------------------------------------ summary


def summary(name: str) -> dict:
    """GET /api/campaign/{name} — the one-call overview (API.md shape)."""
    d = require_campaign(name)
    meta = load_campaign_yaml(name)
    geo = load_geography(name)

    nations = geo.get("nations") or []
    burgs = [{
        "name": b.get("name"),
        "nation": b.get("nation"),
        "population": b.get("population_thousands", b.get("population")),
        "x": b.get("x"),
        "y": b.get("y"),
        "port": bool(b.get("port")),
    } for b in (geo.get("major_settlements") or [])]

    state = load_state(name)
    sim_meta = state.get("meta") or {}
    clock = sim_meta.get("clock") or {}
    factions = state.get("factions") or []

    return {
        "name": meta.get("name") or name,
        "title": meta.get("title") or name,
        "world": {
            "nations": len(nations),
            "burgs": burgs,
        },
        "lore": {
            "timeline_entries": len(load_timeline(name)),
            "entities": len(load_entities(name)),
            "style_files": style_files(name),
        },
        "simulation": {
            "clock": {"week": clock.get("week"), "season": clock.get("season")},
            "factions": [{
                "id": f.get("id"),
                "name": f.get("name"),
                "kind": f.get("kind"),
                "resources": f.get("resources") or {},
            } for f in factions],
            "rumors": len(state.get("rumors") or []),
            "pending": count_pending(name),
        },
        "maps": {
            "cities": _map_slugs(d, "cities"),
            "sites": _map_slugs(d, "sites"),
        },
    }
