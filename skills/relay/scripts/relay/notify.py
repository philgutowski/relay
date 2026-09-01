"""Desktop notifications (U1): the one side effect a reporting component has outside its stream.

macOS only, and off unless the operator asks for them. Two reasons for opt in. A notification is
a side effect on somebody's desktop, which is not a thing a read only verb should do by default.
And it keeps the suite hermetic by construction: no test passes `--notify`, so no test can fire
one, rather than every future test having to remember an off switch.

Both the follower and the runner notify (issue #44). The runner used to be kept out on the
grounds that a failed notification must never touch a run's outcome, but a run launched with
`--detach` and nobody following then notified nobody, which was the whole point of launching it
unattended. The isolation that reasoning called for is kept, in two halves rather than by the
runner staying silent. Nothing here raises: a component that died because a notification failed
would be worse than one that stayed quiet. And `send` is bounded in time, because the runner calls
it synchronously from the loop that renews the Lease, and an `osascript` that never returns is a
failure no exception guard can catch.
"""
import shutil
import subprocess
import sys

BINARY = "osascript"
DARWIN = "darwin"
TITLE = "Relay"
# Long enough for a loaded machine to post a notification, short enough that a hung one costs a
# run less than a poll interval. Sized like the follower's own constants; no run has needed a
# specific value tuned against it.
TIMEOUT_SECONDS = 10


def _quote(text):
    """Escape one AppleScript string literal. Backslashes first: doing the quotes first would
    leave escapes the backslash pass then doubles."""
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def script_for(title, body):
    """The AppleScript one liner, with both literals escaped.

    A Halt class, a task id, or a cause line carrying a double quote would otherwise close the
    literal early and turn the rest of the message into script.
    """
    return 'display notification "%s" with title "%s"' % (_quote(body), _quote(title))


def available(platform=None, which=shutil.which):
    """True only where a notification can actually be delivered. Injected rather than read from
    the module globals so a test can pin both halves without touching the host."""
    name = sys.platform if platform is None else platform
    return name.startswith(DARWIN) and bool(which(BINARY))


def send(title, body, runner=subprocess.run):
    """Fire one notification. An argv list, never a shell string, so nothing in a task id or a
    cause line is interpreted, and bounded in time so a hung binary cannot hold up a caller."""
    try:
        runner([BINARY, "-e", script_for(title, body)], check=False,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
               timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass


def build(enabled, platform=None, which=shutil.which, runner=subprocess.run):
    """The notifier a runner or a follower takes, or None when there is nothing to send to.

    Takes a body and nothing else. The title is always this module's, so binding it here keeps
    the follower from having to know about notifications beyond calling one function.

    None rather than a no op callable: every caller's guard is then one `if`, and a disabled
    notifier is visible to a test by identity rather than by counting calls that did nothing.
    """
    if not enabled or not available(platform=platform, which=which):
        return None
    return lambda body: send(TITLE, body, runner=runner)
