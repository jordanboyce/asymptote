"""Suite-wide fixtures.

The one thing here is audit-log isolation, and it exists because of a real
failure rather than a theoretical one.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


@pytest.fixture(autouse=True, scope="session")
def _isolate_redaction_audit_log(tmp_path_factory):
    """Keep test redactions out of the real ``data/redaction_log.db``.

    Anything that redacts writes to the audit log through the module-level
    singleton, so a test that exercises the boundary appends to the developer's
    actual compliance record. That was not hypothetical: a run of
    ``test_chat_context_redaction.py`` left 120 rows of invented client names
    under collection ``__chat_ctx_test__``, and the Boundary Report renders
    straight off that table — so the junk would have shown up in a prospect
    demo as real redactions of real people.

    Session-scoped and autouse so no test has to remember. Modules that need
    their own log (``test_redaction_http.py``, ``test_boundary_report.py``)
    still override it; a function-scoped fixture wins over this one.
    """
    import services.privacy.redaction_log as rl_mod
    from services.privacy.redaction_log import RedactionLog

    real = rl_mod.redaction_log
    rl_mod.redaction_log = RedactionLog(
        db_path=tmp_path_factory.mktemp("audit") / "redaction_log.db"
    )
    try:
        yield
    finally:
        rl_mod.redaction_log = real
