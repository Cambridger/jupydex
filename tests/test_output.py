from __future__ import annotations

import unittest

from jupydex.output import clean_terminal_output


class OutputTests(unittest.TestCase):
    def test_removes_ansi_and_applies_carriage_return(self) -> None:
        raw = "\x1b[31mred\x1b[0m\nprogress 10%\rprogress 90%\n"
        self.assertEqual(clean_terminal_output(raw), "red\nprogress 90%\n")

    def test_applies_backspace(self) -> None:
        self.assertEqual(clean_terminal_output("ab\bc"), "ac")


if __name__ == "__main__":
    unittest.main()
