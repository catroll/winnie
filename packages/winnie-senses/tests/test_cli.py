"""The CLI must not grow defaults that disagree with Config."""

from __future__ import annotations

import unittest

from winnie_senses.roomd.cli import build_parser
from winnie_senses.roomd.config import Config


class CliDefaultsTest(unittest.TestCase):
    def test_flag_defaults_come_from_config(self) -> None:
        args = build_parser().parse_args([])
        defaults = Config()
        self.assertEqual(args.change_threshold, defaults.change_threshold)
        self.assertEqual(args.target_fps, defaults.target_fps)
        self.assertEqual(args.width, defaults.width)
        self.assertEqual(args.height, defaults.height)
        self.assertEqual(args.port, defaults.port)


if __name__ == "__main__":
    unittest.main()
