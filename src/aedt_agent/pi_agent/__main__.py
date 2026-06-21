"""Backward-compatible CLI module for aedt_agent.pi_agent."""

from aedt_agent.ansys_agent.__main__ import build_parser, main, run

__all__ = ["build_parser", "main", "run"]

if __name__ == "__main__":
    main()
