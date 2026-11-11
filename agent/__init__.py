"""Module C — Agent harness, MCP tools and execution reliability layer.

This package never re-implements planning mathematics. Every number that reaches
an answer must come from the Module B service, which owns the solver, the
scenario ledger and the independent verifier.
"""

__version__ = "0.1.0"
