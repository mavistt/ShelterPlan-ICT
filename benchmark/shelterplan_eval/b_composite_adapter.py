"""B-only structured Composite Recovery transport adapter.

The implementation is kept in bc_adapter for compatibility with the
Stage 3 pilot imports. This explicit module name prevents it from being
mistaken for the natural-language C Agent path.
"""
from .bc_adapter import (  # noqa: F401
    AdapterOutcome,
    build_disaster_recovery_payload,
    normalize_composite_result,
    submit_real_c,
)
