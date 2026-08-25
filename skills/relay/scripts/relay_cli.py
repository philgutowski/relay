#!/usr/bin/env python3
"""Entry point for the Relay runner. Named relay_cli so it never shadows the relay package."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    from relay import cli  # noqa: WPS433 (lands in U10)

    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
