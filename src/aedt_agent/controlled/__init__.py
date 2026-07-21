"""Server-owned, declarative generic AEDT operation primitives."""

from aedt_agent.controlled.read_dispatcher import ControlledProgramError
from aedt_agent.controlled.read_dispatcher import execute_read_program
from aedt_agent.controlled.read_dispatcher import read_program_schema
from aedt_agent.controlled.read_dispatcher import validate_read_program

__all__ = [
    "ControlledProgramError",
    "execute_read_program",
    "read_program_schema",
    "validate_read_program",
]
