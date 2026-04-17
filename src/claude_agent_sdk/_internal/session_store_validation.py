"""Pre-flight validation for ``ClaudeAgentOptions.session_store`` combinations."""

from __future__ import annotations

from ..types import ClaudeAgentOptions, SessionStore


def _store_implements(store: SessionStore, method: str) -> bool:
    """True if ``store`` overrides ``method`` rather than inheriting the
    Protocol default that raises :class:`NotImplementedError`."""
    impl = getattr(store, method, None)
    if impl is None:
        return False
    default = getattr(SessionStore, method, None)
    return getattr(type(store), method, None) is not default


def validate_session_store_options(options: ClaudeAgentOptions) -> None:
    """Raise :class:`ValueError` for invalid ``session_store`` option combinations.

    Called before subprocess spawn so misconfiguration fails fast instead of
    surfacing as a confusing runtime error mid-session.
    """
    store = options.session_store
    if store is None:
        return

    if (
        options.continue_conversation
        and options.resume is None
        and not _store_implements(store, "list_sessions")
    ):
        # When resume is explicitly set, list_sessions() is provably never
        # called (resume wins over continue), so a minimal store is fine.
        raise ValueError(
            "continue_conversation with session_store requires the store to "
            "implement list_sessions()"
        )

    if options.enable_file_checkpointing:
        raise ValueError(
            "session_store cannot be combined with enable_file_checkpointing "
            "(checkpoints are local-disk only and would diverge from the "
            "mirrored transcript)"
        )
