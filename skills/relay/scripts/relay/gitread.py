"""Read-only git wrappers. Every call is an argument list, never a shell string (R9).

Nothing here changes a branch or a working tree. `fetch` updates the remote tracking refs only,
which the runner needs before it can compare local and remote heads. The mutating wrappers
(checkout, merge, push) live in gitwrite.py from U8 onward, so a reader of the run loop can see
at a glance which calls can move the repo.
"""
import subprocess

GIT_TIMEOUT_SECONDS = 120


class GitError(RuntimeError):
    """A git command exited nonzero. Carries the args and stderr for the summary."""

    def __init__(self, args, returncode, stderr):
        super().__init__("git %s exited %d: %s" % (" ".join(args), returncode, stderr.strip()))
        self.args_list = list(args)
        self.returncode = returncode
        self.stderr = stderr


def run(repo, args, check=True, env=None, timeout=GIT_TIMEOUT_SECONDS):
    """Run `git -C <repo> <args>` and return the CompletedProcess. Raises GitError on nonzero
    exit when check is true."""
    cmd = ["git", "-C", repo] + list(args)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=timeout, stdin=subprocess.DEVNULL
    )
    if check and proc.returncode != 0:
        raise GitError(cmd, proc.returncode, proc.stderr)
    return proc


def status_porcelain(repo):
    """The `git status --porcelain` text; empty when the tree is clean."""
    return run(repo, ["status", "--porcelain"]).stdout


def is_clean(repo):
    return status_porcelain(repo).strip() == ""


def status_paths(repo):
    """Every path the working tree reports as changed, and which of those are untracked.

    Returns (paths, untracked). `-z` rather than the default, so a path holding a space or a
    quote arrives whole instead of quoted, and `-uall` so a new directory is reported by its
    files rather than by its name.
    """
    fields = [field for field in
              run(repo, ["status", "--porcelain", "-z", "-uall"]).stdout.split("\0") if field]
    paths, untracked = [], []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        code, path = entry[:2], entry[3:]
        if not path:
            continue
        paths.append(path)
        if code == "??":
            untracked.append(path)
        elif code[0] in ("R", "C") and index < len(fields):
            # A rename or a copy puts the destination in this field and the source in the next.
            paths.append(fields[index])
            index += 1
    return paths, untracked


def current_branch(repo):
    """The branch name, or `HEAD` when detached."""
    return run(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()


def rev_parse(repo, ref):
    """The full SHA for a ref, or None when the ref does not resolve."""
    proc = run(repo, ["rev-parse", "--verify", "--quiet", ref + "^{commit}"], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def fetch(repo, remote="origin", env=None):
    run(repo, ["fetch", "--quiet", remote], env=env)


def branch_exists(repo, name):
    proc = run(repo, ["show-ref", "--verify", "--quiet", "refs/heads/" + name], check=False)
    return proc.returncode == 0


def show(repo, ref, path):
    """The contents of `path` at `ref`, or None when either does not exist."""
    proc = run(repo, ["show", "%s:%s" % (ref, path)], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout


def remotes(repo):
    text = run(repo, ["remote"]).stdout
    return [line.strip() for line in text.splitlines() if line.strip()]


def default_branch(repo, remote="origin"):
    """The remote's default branch from refs/remotes/<remote>/HEAD, or None when unset.
    A bare origin added with `remote add` never gets this ref; tests set it explicitly."""
    proc = run(repo, ["symbolic-ref", "--quiet", "refs/remotes/%s/HEAD" % remote], check=False)
    if proc.returncode != 0:
        return None
    ref = proc.stdout.strip()
    prefix = "refs/remotes/%s/" % remote
    return ref[len(prefix):] if ref.startswith(prefix) else None


def diff_name_only(repo, base, head):
    """Paths that differ between two refs, one per entry."""
    text = run(repo, ["diff", "--name-only", "%s..%s" % (base, head)]).stdout
    return [line for line in text.splitlines() if line]


def paths_touched_in_range(repo, base, head):
    """Every path any commit in base..head touched, including files later deleted.

    `diff_name_only` compares tip trees, so a file added after base and removed before
    head is invisible there. A merge still publishes that commit, so the Task path bound
    has to see it.
    """
    text = run(repo, ["log", "--name-only", "--pretty=format:", "%s..%s" % (base, head)]).stdout
    seen = []
    for line in text.splitlines():
        if line and line not in seen:
            seen.append(line)
    return seen


def config_get(repo, key, env=None):
    """A git config value as seen from inside the repo, or None when unset."""
    proc = run(repo, ["config", "--get", key], check=False, env=env)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def merge_head_exists(repo):
    """True when a merge is in progress (R55 uses this to abort a dangling merge)."""
    proc = run(repo, ["rev-parse", "--verify", "--quiet", "MERGE_HEAD"], check=False)
    return proc.returncode == 0


def log_messages(repo, base, head):
    """[(full sha, full message)] for base..head, newest first. The full message because a
    closing trailer such as `Closes #62` lives in the body, not the subject."""
    text = run(repo, ["log", "--format=%H%x00%B%x1e", "%s..%s" % (base, head)]).stdout
    entries = []
    for chunk in text.split("\x1e"):
        sha, _, message = chunk.strip("\n").partition("\x00")
        if sha.strip():
            entries.append((sha.strip(), message.strip()))
    return entries


def log_oneline(repo, base, head):
    text = run(repo, ["log", "--oneline", "%s..%s" % (base, head)]).stdout
    return [line for line in text.splitlines() if line]
