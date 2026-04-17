"""Test utilities for SDK extension authors.

Importing this subpackage does not require ``pytest`` — assertions use plain
``assert`` so the harness works under any test runner.
"""

from .session_store_conformance import run_session_store_conformance

__all__ = ["run_session_store_conformance"]
