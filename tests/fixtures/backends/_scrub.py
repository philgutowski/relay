"""Scrub private content out of the captured fixtures without changing their shape.

The blocked tasks were told to find a Slack webhook the project does not hold, and one of them
searched the operator's whole home directory before concluding that. Its transcript therefore
carries file paths and prose from unrelated private work. These fixtures exist to exercise the
per-backend normalizers in U6, which read event types and structure rather than the content of a
search result, so redacting the payload costs the fixture nothing it is used for.

Every substitution is length-preserving in kind, not in bytes: a redacted path is still a path,
so a parser that walks the structure sees what it saw before.
"""
import json
import os
import re
import sys

ROOT = "tests/fixtures/backends"
EMAIL = "relay@example.com"

# Order matters: the path rule runs first so a term inside a path is already gone.
SUBS = [
    # Any absolute path under the operator's home that is not the throwaway proof target.
    (re.compile(r"/Users/pgutowski/(?!Documents/PhilAI/relay)[\w./\-]*"), "/redacted/path"),
    (re.compile(r"/Users/pgutowski/Documents/PhilAI/(?!relay)[\w./\-]*"), "/redacted/path"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), EMAIL),
    (re.compile(r"\b(?:Integrel|integrel)\b"), "redacted"),
    (re.compile(r"\b(?:Voltstream|voltstream)\b"), "redacted"),
    (re.compile(r"\berev\b"), "redacted"),
    (re.compile(r"\b(?:harbor-office|alert-relay|distiller)\b"), "redacted"),
    (re.compile(r"\bworkbench\b"), "redacted"),
    (re.compile(r"\b(?:caddy|squid)\b"), "redacted"),
    (re.compile(r"\bDJ_tools\b"), "redacted"),
    (re.compile(r"\bElectric Passage\b"), "redacted"),
]


def scrub(text):
    for rx, replacement in SUBS:
        text = rx.sub(replacement, text)
    return text


def main():
    changed = []
    for backend in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, backend)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            path = os.path.join(d, name)
            with open(path, encoding="utf-8", errors="replace") as fh:
                original = fh.read()
            cleaned = scrub(original)
            if cleaned == original:
                continue
            # A jsonl fixture must still decode exactly as many lines as it did before. Codex
            # prints one non-JSON line onto its own JSON stream, so the test is that the count
            # is unchanged rather than that every line parses.
            if path.endswith(".jsonl"):
                def decodable(text):
                    n = 0
                    for line in text.splitlines():
                        if not line.strip():
                            continue
                        try:
                            json.loads(line)
                        except Exception:
                            continue
                        n += 1
                    return n
                before, after = decodable(original), decodable(cleaned)
                if before != after:
                    raise SystemExit("scrub changed decodable line count in %s: %d to %d"
                                     % (path, before, after))
            with open(path, "w", encoding="utf-8") as out:
                out.write(cleaned)
            changed.append(path)
    for path in changed:
        print("scrubbed", path)
    print("\n%d file(s) changed" % len(changed))


if __name__ == "__main__":
    main()
