"""Temp git repo fixture helper shared by every test that needs a target repo.

Builds every repo the same way (plan U8 step 10): `git init -b main`, a local identity, one
initial commit, a bare `origin` the repo has pushed to, `refs/remotes/origin/HEAD` set to main
(a bare origin added with `remote add` never gets it), and an optional executable pre-push hook.
"""
import os
import subprocess


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, check=check,
                          stdin=subprocess.DEVNULL)


def make_repo(base_dir, name="repo", identity=True, origin=True, pre_push=None, files=None):
    """Create <base_dir>/<name> with an initial commit. Returns the repo path.

    identity: set a local user.name and user.email (False leaves the repo without one, and the
      test environment is scrubbed of the global identity so the check is real).
    origin: create <base_dir>/<name>.git as a bare remote, push main, set origin/HEAD.
    pre_push: hook script text written executable to .git/hooks/pre-push.
    files: {relative path: text} written and included in the initial commit.
    """
    repo = os.path.join(base_dir, name)
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    if identity:
        git(repo, "config", "user.name", "Relay Test")
        git(repo, "config", "user.email", "relay@example.invalid")
    for rel, text in (files or {"README.md": "# fixture\n"}).items():
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
    git(repo, "add", "-A")
    commit_env = dict(os.environ)
    if not identity:
        commit_env.update({
            "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@example.invalid",
            "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@example.invalid",
        })
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "initial"], check=True, env=commit_env,
                   capture_output=True, stdin=subprocess.DEVNULL)
    if origin:
        bare = os.path.join(base_dir, name + ".git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True, capture_output=True)
        git(repo, "remote", "add", "origin", bare)
        git(repo, "push", "-q", "origin", "main")
        git(repo, "remote", "set-head", "origin", "main")
    if pre_push:
        hook = os.path.join(repo, ".git", "hooks", "pre-push")
        with open(hook, "w") as handle:
            handle.write(pre_push)
        os.chmod(hook, 0o755)
    return repo


def scrubbed_env():
    """An environment with no global git identity, so an identity check sees only the repo."""
    env = dict(os.environ)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["HOME"] = env.get("RELAY_TEST_HOME", env["HOME"])
    return env
