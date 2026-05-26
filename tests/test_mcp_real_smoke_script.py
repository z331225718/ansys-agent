import subprocess
import sys


def test_mcp_real_smoke_help_lists_adapter_and_graphical_options():
    result = subprocess.run(
        [sys.executable, "scripts/run_mcp_real_smoke.py", "--help"],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "--adapter" in result.stdout
    assert "--graphical" in result.stdout
    assert "--include-experimental" in result.stdout
