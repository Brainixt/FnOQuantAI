"""
================================================================================
FnOQuantAI Unit Tests: CLI Management & Entrypoint
================================================================================
Tests:
1. Argument parsing & subcommands: status, scan, monitor, report, analytics.
2. JSON export and formatted text output from CLI.
3. SingleInstanceLock acquisition and release behavior.
================================================================================
"""

import os
import sys
import json
import unittest
import subprocess
from unittest.mock import patch, MagicMock

from main import SingleInstanceLock, main

PROJECT_DIR = r"D:\SM_Server\MarketTool\FnOQuantAI"


class TestCLIEntrypoint(unittest.TestCase):
    """Tests command-line interface execution and subcommands."""

    def test_single_instance_lock(self):
        """Verifies lock file creation, active PID check, and cleanup."""
        test_lock_file = os.path.join(PROJECT_DIR, "test_unit.lock")
        lock = SingleInstanceLock(lock_path=test_lock_file)

        # 1. First acquire must succeed
        self.assertTrue(lock.acquire())
        self.assertTrue(os.path.exists(test_lock_file))

        # 2. Second acquire with current running PID must return False
        lock2 = SingleInstanceLock(lock_path=test_lock_file)
        self.assertFalse(lock2.acquire())

        # 3. Release cleans up
        lock.release()
        self.assertFalse(os.path.exists(test_lock_file))

    def test_cli_help(self):
        """Verifies CLI help output."""
        with patch.object(sys, "argv", ["main.py", "--help"]):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)

    def test_cli_status(self):
        """Verifies status subcommand executes cleanly."""
        with patch.object(sys, "argv", ["main.py", "status"]):
            code = main()
            self.assertEqual(code, 0)

    def test_cli_scan(self):
        """Verifies scan subcommand executes cleanly."""
        with patch.object(sys, "argv", ["main.py", "scan"]):
            code = main()
            self.assertEqual(code, 0)

    def test_cli_monitor(self):
        """Verifies monitor subcommand executes cleanly."""
        with patch.object(sys, "argv", ["main.py", "monitor"]):
            code = main()
            self.assertEqual(code, 0)

    def test_cli_report_text_and_json(self):
        """Verifies report generation in both text and JSON formats."""
        # Text format
        with patch.object(sys, "argv", ["main.py", "report"]):
            code = main()
            self.assertEqual(code, 0)

        # JSON format
        with patch.object(sys, "argv", ["main.py", "report", "--export", "json"]):
            code = main()
            self.assertEqual(code, 0)

    def test_cli_analytics(self):
        """Verifies analytics subcommand execution."""
        with patch.object(sys, "argv", ["main.py", "analytics"]):
            code = main()
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
