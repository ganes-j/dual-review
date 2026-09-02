import subprocess


def run(prompt, model, hermes_bin="hermes"):
    """Run Hermes as an isolated one-shot worker and return only its text output.

    The prompt travels on stdin rather than argv so a large PR diff cannot hit the
    platform command-length limit. Safe mode prevents project rules, plugins, and
    MCP servers from influencing the reviewer; model selection stays explicit so
    the Hermes lane can remain independent from Codex.
    """
    command = [
        hermes_bin, "chat", "--query-file", "-", "--oneshot", "--quiet",
        "--safe-mode", "--source", "tool", "--model", model,
    ]
    try:
        proc = subprocess.run(command, input=prompt, capture_output=True, text=True)
    except OSError:
        return ""
    return getattr(proc, "stdout", "") or ""
