import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
import hermes_runner  # noqa: E402


class TestHermesRunner(unittest.TestCase):
    @mock.patch("hermes_runner.subprocess.run")
    def test_runs_isolated_oneshot_with_stdin_prompt_and_model(self, run):
        run.return_value.stdout = "[]"

        output = hermes_runner.run("review this untrusted diff", model="anthropic/claude-sonnet-4")

        command = run.call_args.args[0]
        self.assertEqual(command[:5], ["hermes", "chat", "--query-file", "-", "--oneshot"])
        self.assertIn("--quiet", command)
        self.assertIn("--safe-mode", command)
        self.assertEqual(command[command.index("--model") + 1], "anthropic/claude-sonnet-4")
        self.assertEqual(run.call_args.kwargs["input"], "review this untrusted diff")
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(output, "[]")

    @mock.patch("hermes_runner.subprocess.run", side_effect=OSError)
    def test_missing_hermes_binary_degrades_to_empty_output(self, run):
        self.assertEqual(hermes_runner.run("prompt", model="provider/model"), "")


if __name__ == "__main__":
    unittest.main()
