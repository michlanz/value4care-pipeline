"""Test minimo della CLI."""

from __future__ import annotations

import unittest

from tests import _bootstrap  # noqa: F401

from interface.cli import build_parser


class CLITestCase(unittest.TestCase):
    """Controlli di base sull'interfaccia da terminale."""

    def test_health_command_is_available(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["health"])
        self.assertEqual(args.command, "health")

    def test_list_pdfs_accepts_family_filter(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["list-pdfs", "--family", "vaccination_certificate"])
        self.assertEqual(args.command, "list-pdfs")
        self.assertEqual(args.family, "vaccination_certificate")


if __name__ == "__main__":
    unittest.main()
