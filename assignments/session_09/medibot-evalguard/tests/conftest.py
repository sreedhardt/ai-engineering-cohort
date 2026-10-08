"""Keep the test suite offline: no traces are sent to LangSmith."""

import pytest


@pytest.fixture(autouse=True)
def _no_tracing(monkeypatch):
    # traced() looks configure() up at call time, so this disables every span.
    monkeypatch.setattr("evalguard.observability.tracing.configure", lambda: False)
