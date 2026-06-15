"""Allow `python -m aedt_agent.agent` to invoke the CLI."""

from aedt_agent.agent.cli import run
import sys

sys.exit(run())
