#!/usr/bin/env python3
"""Entry point for the Relay runner. Named relay_cli so it never shadows the relay package."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    # Imported here rather than at module scope, so sys.path is set before the package is
    # found.
    from relay import cli

    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
