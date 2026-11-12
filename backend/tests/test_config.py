import dataclasses
import logging

from app.config import DEFAULT_PEPPER, Settings
from app.main import create_app


def _app(pepper, caplog):
    s = dataclasses.replace(Settings.from_env(), pepper=pepper)
    with caplog.at_level(logging.WARNING, logger="tracker"):
        create_app(s)
    return [r.getMessage() for r in caplog.records if r.name == "tracker"]


def test_default_pepper_logs_a_loud_warning(caplog):
    msgs = _app(DEFAULT_PEPPER, caplog)
    assert any("SERVER_PEPPER is the public default" in m for m in msgs), msgs


def test_custom_pepper_is_silent_and_never_logged(caplog):
    msgs = _app("a-long-random-value-xyz", caplog)
    assert not any("SERVER_PEPPER" in m or "a-long-random-value-xyz" in m for m in msgs), msgs
