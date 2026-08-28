"""Desktop notifications (U1): the follower's one side effect outside its own stream.

macOS only, and off unless the operator asks for them. Two reasons for opt in. A notification is
a side effect on somebody's desktop, which is not a thing a read only verb should do by default.
And it keeps the suite hermetic by construction: no test passes `--notify`, so no test can fire
one, rather than every future test having to remember an off switch.

The notifier is the follower's, not the runner's. The follower is the component a human is
attached to, and keeping the desktop out of the run loop means a failed notification can never
touch a run's outcome. The cost is that a run launched with `--detach` and nobody following
notifies nobody.

Nothing here raises. A follower that died because a notification failed would be worse than one
that stayed quiet.
"""
import shutil
import subprocess
import sys

BINARY = "osascript"
DARWIN = "darwin"
TITLE = "Relay"


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
    cause line is interpreted."""
    try:
        runner([BINARY, "-e", script_for(title, body)], check=False,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def build(enabled, platform=None, which=shutil.which, runner=subprocess.run):
    """The notifier a follower takes, or None when there is nothing to send to.

    None rather than a no op callable: every caller's guard is then one `if`, and a disabled
    notifier is visible to a test by identity rather than by counting calls that did nothing.
    """
    if not enabled or not available(platform=platform, which=which):
        return None
    return lambda title, body: send(title, body, runner=runner)
