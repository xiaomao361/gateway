"""Context compression boundary.

The first release deliberately keeps provider output intact.  The stable
interface lives here so a later compressor can be enabled without changing
the public MCP tools.
"""
from __future__ import annotations


def compress_context(context: dict, enabled: bool = False) -> dict:
    """Return context unchanged until a reviewed compression policy exists."""
    return context
