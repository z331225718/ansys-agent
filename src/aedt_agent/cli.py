"""Default CLI compatibility module for the Agent product."""

from aedt_agent.agent.cli import build_parser, main, run

__all__ = ["build_parser", "main", "run"]


if __name__ == "__main__":
    main()
