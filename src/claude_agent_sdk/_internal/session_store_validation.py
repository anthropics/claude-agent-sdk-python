"""Pre-flight validation for ``ClaudeAgentOptions.session_store`` combinations."""

from __future__ import annotations

from ..types import ClaudeAgentOptions, SessionStore


def _store_implements(store: SessionStore, method: str) -> bool:
    """True if ``store`` provides ``method`` rather than inheriting the
    Protocol default that raises :class:`NotImplementedError`.

    Resolved against the instance rather than the class: ``SessionStore`` is a
    structural Protocol, so an implementation assigned in ``__init__``
    (delegation, ``functools.partial``, a test double) counts just as much as a
    class-level ``def``.
    """
    impl = getattr(store, method, None)
    if impl is None:
        return False
    default = getattr(SessionStore, method, None)
    # Compare the underlying function so a bound method is matched against the
    # Protocol default; anything that is not a bound method (a plain callable
    # assigned on the instance) is compared directly and is never the default.
    return getattr(impl, "__func__", impl) is not default


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
