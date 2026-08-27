"""Manifest loader and validator (U2).

A manifest is TOML (KTD2): it has no executable constructs, so R9 reduces to type checks. The
loader turns the file into frozen dataclasses (a dataclass is a plain record type; frozen means
no code can mutate it after load, so every unit sees the same manifest). Nothing is defaulted
silently: every default applied is named in the validation result (KTD11).

Two entry points. `load(path)` parses and shapes; it raises ManifestError only when the file
cannot be read or a required table is missing. `validate(manifest)` applies every rule from the
plan and returns a ValidationResult with errors, warnings, the defaults applied, and the
completed closeout allowed paths. `validate` reads the target repo through gitread for the
remote, identity, and CE artifact root checks; pass `check_repo=False` to skip those.
"""
import os
import re
import tomllib
from dataclasses import dataclass, field

from . import contracts, gitread

ADAPTERS = ("jira", "github", "markdown")
SHIPPING_MODES = ("local_merge", "pr_terminal")
# Named in the schema and refused by `validate`. Every read side piece of pr_terminal exists and
# is unit tested (`gitwrite.find_pr`, `gitwrite.poll_ci`, the `pr_probe` seam in `verify.verify`)
# and nothing wires them into the run loop, so a run under this mode halts on its first task
# with a class whose documented remedy is wrong for the real cause. Refusing it before a run
# starts is the honest place to say so. Decided 2026-08-26; see
# docs/ideation/2026-08-25-relay-review-residuals.md.
UNIMPLEMENTED_SHIPPING_MODES = ("pr_terminal",)
QUALIFYING_KEYS = ("gate", "durable_state", "independence", "editors")
REQUIRED_TABLES = ("project", "tracker", "shipping", "permissions", "gate", "qualifying", "tasks")


class ManifestError(ValueError):
    """The file is not a manifest at all: unreadable, not TOML, or missing a required table."""


@dataclass(frozen=True)
class Project:
    repo: str
    default_branch: str | None
    mirror: tuple


@dataclass(frozen=True)
class Tracker:
    adapter: str
    site: str | None
    project_key: str | None
    owner: str | None
    project_number: int | None
    status_field: str | None
    file: str | None
    token_env: str
    email_env: str
    done_statuses: tuple
    in_review_status: str | None


@dataclass(frozen=True)
class Permissions:
    allowed: tuple
    disallowed: tuple


@dataclass(frozen=True)
class Timeouts:
    task_minutes: int
    closeout_minutes: int
    ci_poll_minutes: int


@dataclass(frozen=True)
class Closeout:
    model: str
    effort: str
    allowed_tools: tuple
    allowed_paths: tuple


@dataclass(frozen=True)
class Gate:
    command: object
    description: str


@dataclass(frozen=True)
class Qualifying:
    gate: str
    durable_state: str
    independence: str
    editors: str


@dataclass(frozen=True)
class OnBlocked:
    merge_partial: bool
    open_followup: bool


@dataclass(frozen=True)
class Task:
    id: str
    model: str
    effort: str
    excluded: bool
    reason: str | None


@dataclass(frozen=True)
class Manifest:
    path: str
    project: Project
    tracker: Tracker
    shipping_mode: str
    permissions: Permissions
    timeouts: Timeouts
    closeout: Closeout
    gate: Gate
    qualifying: Qualifying
    on_blocked: OnBlocked
    tasks: tuple
    raw: dict = field(repr=False, compare=False)
    defaults_applied: tuple = ()


@dataclass
class ValidationResult:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    defaults_applied: list = field(default_factory=list)
    allowed_paths: list = field(default_factory=list)
    disallowed: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def _tuple(value):
    return tuple(value) if isinstance(value, list) else value


def load(path):
    """Parse a manifest file into a Manifest. Defaults are applied here and named in
    `defaults_applied`, so validate can report them; missing optional tables become empty."""
    path = os.path.abspath(path)
    try:
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError:
        raise ManifestError("manifest not found: %s" % path)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError("manifest is not valid TOML: %s" % exc)
    missing = [name for name in REQUIRED_TABLES if name not in raw]
    if missing:
        raise ManifestError("manifest is missing required tables: %s" % ", ".join(missing))

    defaults = []

    def default(table, key, value):
        defaults.append("%s.%s = %r" % (table, key, value))
        return value

    def pick(table, name, key, value):
        """The manifest's value when the key is present, even 0 or empty, else the named
        default. Truthiness would let an explicit 0 slip past validate's positive rule."""
        return table[key] if key in table else default(name, key, value)

    p = raw["project"]
    t = raw["tracker"]
    perms = raw["permissions"]
    timeouts = raw.get("timeouts", {})
    closeout = raw.get("closeout", {})
    gate = raw["gate"]
    q = raw["qualifying"]
    ob = raw.get("on_blocked", {})

    project = Project(
        repo=os.path.expanduser(str(p.get("repo", ""))),
        default_branch=p.get("default_branch"),
        mirror=_tuple(p.get("mirror", [])),
    )
    tracker = Tracker(
        adapter=t.get("adapter"),
        site=t.get("site"),
        project_key=t.get("project_key"),
        owner=t.get("owner"),
        project_number=t.get("project_number"),
        status_field=t.get("status_field"),
        file=t.get("file"),
        # The credential names are a Jira fact. Naming them as applied defaults on a GitHub or
        # markdown manifest told the first Cratekit run's operator about tokens it never reads.
        token_env=(pick(t, "tracker", "token_env", "JIRA_API_TOKEN")
                   if t.get("adapter") == "jira" else str(t.get("token_env", ""))),
        email_env=(pick(t, "tracker", "email_env", "JIRA_EMAIL")
                   if t.get("adapter") == "jira" else str(t.get("email_env", ""))),
        done_statuses=_tuple(t.get("done_statuses", [])),
        in_review_status=t.get("in_review_status"),
    )
    permissions = Permissions(
        allowed=_tuple(perms.get("allowed", [])),
        disallowed=_tuple(perms.get("disallowed", [])),
    )
    timeouts_obj = Timeouts(
        task_minutes=pick(timeouts, "timeouts", "task_minutes", contracts.DEFAULT_TASK_TIMEOUT_MINUTES),
        closeout_minutes=pick(timeouts, "timeouts", "closeout_minutes", contracts.DEFAULT_CLOSEOUT_TIMEOUT_MINUTES),
        ci_poll_minutes=pick(timeouts, "timeouts", "ci_poll_minutes", contracts.DEFAULT_CI_POLL_MINUTES),
    )
    closeout_obj = Closeout(
        model=pick(closeout, "closeout", "model", contracts.DEFAULT_CLOSEOUT_MODEL),
        effort=pick(closeout, "closeout", "effort", contracts.DEFAULT_CLOSEOUT_EFFORT),
        allowed_tools=_tuple(closeout.get("allowed_tools", [])),
        allowed_paths=_tuple(closeout.get("allowed_paths", [])),
    )
    gate_obj = Gate(command=_tuple(gate.get("command")), description=str(gate.get("description", "")))
    qualifying = Qualifying(**{key: str(q.get(key, "")).strip() for key in QUALIFYING_KEYS})
    on_blocked = OnBlocked(
        merge_partial=bool(pick(ob, "on_blocked", "merge_partial", False)),
        open_followup=bool(pick(ob, "on_blocked", "open_followup", False)),
    )
    raw_tasks = raw.get("tasks", [])
    if not isinstance(raw_tasks, list) or not all(isinstance(entry, dict) for entry in raw_tasks):
        raise ManifestError("tasks must be an array of tables ([[tasks]]), not a single [tasks] table")
    tasks = tuple(
        Task(
            id=str(entry.get("id", "")),
            model=str(entry.get("model", "")),
            effort=str(entry.get("effort", "")),
            excluded=bool(entry.get("excluded", False)),
            reason=entry.get("reason"),
        )
        for entry in raw_tasks
    )
    return Manifest(
        path=path,
        project=project,
        tracker=tracker,
        shipping_mode=raw["shipping"].get("mode"),
        permissions=permissions,
        timeouts=timeouts_obj,
        closeout=closeout_obj,
        gate=gate_obj,
        qualifying=qualifying,
        on_blocked=on_blocked,
        tasks=tasks,
        raw=raw,
        defaults_applied=tuple(defaults),
    )


def _is_string_list(value):
    return isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value)


def docs_root_for(repo):
    """The CE artifact root from the target's .compound-engineering/config.yaml, else `docs`.
    Read with a regex rather than a YAML parser because the runner has no YAML library."""
    for name in ("config.local.yaml", "config.yaml"):
        path = os.path.join(repo, ".compound-engineering", name)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                match = re.match(r"^\s*docs_root\s*:\s*['\"]?([^'\"#\n]+?)['\"]?\s*(#.*)?$", line)
                if match:
                    return match.group(1).strip().rstrip("/")
    return contracts.DEFAULT_DOCS_ROOT


def completed_allowed_paths(manifest, docs_root):
    """R53: the closeout may only touch the CE artifact root, CONCEPTS.md, the markdown tracker
    file, and whatever the manifest adds."""
    paths = [docs_root.rstrip("/") + "/", contracts.CONCEPTS_FILE]
    if manifest.tracker.adapter == "markdown" and manifest.tracker.file:
        paths.append(manifest.tracker.file)
    for extra in manifest.closeout.allowed_paths:
        if extra not in paths:
            paths.append(extra)
    return paths


def validate(manifest, check_repo=True, env=None):
    """Apply every rule from plan U2 step 2. Returns a ValidationResult; never raises for a
    rule failure, so the CLI can print every problem at once."""
    result = ValidationResult(defaults_applied=list(manifest.defaults_applied))
    err = result.errors.append
    warn = result.warnings.append

    # R9: the two executed fields are argument lists, never shell strings.
    if not _is_string_list(manifest.gate.command) or not manifest.gate.command:
        err("gate.command must be a non-empty array of strings (an argument list, never a shell string)")
    if not _is_string_list(manifest.project.mirror):
        err("project.mirror must be an array of strings (an argument list, never a shell string)")

    # R10, R11: dontAsk only; the disallow list carries every R10 variant.
    raw_perms = manifest.raw.get("permissions", {})
    if "permission_mode" in raw_perms or "mode" in raw_perms:
        err("permissions.permission_mode is not a field; Relay always runs dontAsk (R11)")
    if not _is_string_list(manifest.permissions.allowed) or not manifest.permissions.allowed:
        err("permissions.allowed must be a non-empty array of tool names")
    elif any(contracts.FORBIDDEN_PERMISSION_MODE in item for item in manifest.permissions.allowed):
        err("permissions.allowed must not name %s (R11)" % contracts.FORBIDDEN_PERMISSION_MODE)
    disallowed = list(manifest.permissions.disallowed) if _is_string_list(manifest.permissions.disallowed) else []
    for pattern in contracts.DISALLOWED_TOOLS:
        if pattern not in disallowed:
            disallowed.append(pattern)
            warn("permissions.disallowed was missing %s; added" % pattern)
    result.disallowed = disallowed

    # R3, R56: four qualifying sentences, each non-empty.
    for key in QUALIFYING_KEYS:
        if not getattr(manifest.qualifying, key):
            err("qualifying.%s has no satisfier; every qualifying property needs a stated sentence (R3)" % key)

    # Shipping mode and adapter.
    if manifest.shipping_mode not in SHIPPING_MODES:
        err("shipping.mode must be one of %s" % ", ".join(SHIPPING_MODES))
    elif manifest.shipping_mode in UNIMPLEMENTED_SHIPPING_MODES:
        err("shipping.mode %s is not implemented: the run loop has no pull request sequence, so "
            "every task would halt without one being opened or checked. Use local_merge."
            % manifest.shipping_mode)
    adapter = manifest.tracker.adapter
    if adapter not in ADAPTERS:
        err("tracker.adapter must be one of %s" % ", ".join(ADAPTERS))
    elif adapter == "jira":
        for key in ("site", "project_key"):
            if not getattr(manifest.tracker, key):
                err("tracker.%s is required for the jira adapter" % key)
        if not manifest.tracker.done_statuses:
            err("tracker.done_statuses is required for the jira adapter")
    elif adapter == "github":
        for key in ("owner", "project_number"):
            if getattr(manifest.tracker, key) in (None, ""):
                err("tracker.%s is required for the github adapter" % key)
    elif adapter == "markdown":
        if not manifest.tracker.file:
            err("tracker.file is required for the markdown adapter")
    if manifest.shipping_mode == "local_merge" and not manifest.tracker.in_review_status:
        err("tracker.in_review_status is required in local_merge mode (KTD6 uses it to route a missing envelope)")
    elif manifest.shipping_mode == "local_merge" and adapter == "markdown":
        # Finding 20, decided 2026-08-26. The markdown line has two states, open and closed, and
        # the adapter reads it at the remote default branch head, which a task branch never
        # reaches before the merge. KTD6's rescue route for a missing envelope therefore cannot
        # fire under this adapter, and the manifest field only names the status the brief tells
        # the task to write. Say so rather than let an operator wait for a route that never comes.
        warn("tracker.in_review_status is %r, but the markdown adapter reports only open or closed, "
             "so KTD6's route for a task that exits without an envelope cannot fire; such a task is "
             "always treated as blocked under this adapter" % manifest.tracker.in_review_status)

    # R2, R5: every task has id, model, effort; an excluded task has a reason.
    seen = set()
    for index, task in enumerate(manifest.tasks):
        label = "tasks[%d]" % index
        for key in ("id", "model", "effort"):
            if not getattr(task, key):
                err("%s.%s is required" % (label, key))
        if task.id in seen:
            err("%s.id %r is listed twice" % (label, task.id))
        seen.add(task.id)
        if task.excluded and not (task.reason or "").strip():
            err("%s (%s) is excluded but carries no reason (R5)" % (label, task.id or "?"))
    if not manifest.tasks:
        err("tasks is empty")

    # Timeouts: positive integers, and the lease TTL must stay below the task timeout (R47).
    for key in ("task_minutes", "closeout_minutes", "ci_poll_minutes"):
        value = getattr(manifest.timeouts, key)
        if not isinstance(value, int) or value <= 0:
            err("timeouts.%s must be a positive integer" % key)
    if isinstance(manifest.timeouts.task_minutes, int) and manifest.timeouts.task_minutes * 60 <= contracts.LEASE_TTL_SECONDS:
        err("timeouts.task_minutes must exceed the lease TTL of %d seconds" % contracts.LEASE_TTL_SECONDS)

    # Repo checks.
    repo = manifest.project.repo
    if not repo or not os.path.isdir(os.path.join(repo, ".git")):
        err("project.repo is not a git repository: %r" % repo)
        check_repo = False
    docs_root = contracts.DEFAULT_DOCS_ROOT
    if check_repo:
        remotes = gitread.remotes(repo)
        if manifest.shipping_mode == "local_merge" and "origin" not in remotes:
            err("shipping.mode local_merge requires an origin remote to push to")
        for key in ("user.name", "user.email"):
            if not gitread.config_get(repo, key, env):
                err("git config %s does not resolve in %s; the runner's merge authors a commit" % (key, repo))
        if manifest.project.default_branch is None and gitread.default_branch(repo) is None:
            err("project.default_branch is unset and refs/remotes/origin/HEAD is not set in the repo")
        docs_root = docs_root_for(repo)
    result.allowed_paths = completed_allowed_paths(manifest, docs_root)
    return result


def resolved_disallowed(manifest):
    """The disallow list with every R10 variant present, for the launcher."""
    return validate(manifest, check_repo=False).disallowed
