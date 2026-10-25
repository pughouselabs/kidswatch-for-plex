"""Bad install settings fail before the keeper can start a busy loop."""
import pytest

from app.config import load


@pytest.mark.parametrize("value", ["0", "-1", "", " ", "soon", "1.5", "NaN", "+20", "２０"])
def test_poll_interval_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("PK_POLL_SECONDS", value)
    with pytest.raises(ValueError, match=r"PK_POLL_SECONDS.*positive whole.*1 or greater"):
        load()


@pytest.mark.parametrize("value, expected", [("1", 1), ("20", 20), (" 60 ", 60), ("300", 300)])
def test_poll_interval_accepts_positive_whole_seconds(monkeypatch, value, expected):
    monkeypatch.setenv("PK_POLL_SECONDS", value)
    assert load().poll_seconds == expected


def test_poll_interval_defaults_to_twenty_seconds(monkeypatch):
    monkeypatch.delenv("PK_POLL_SECONDS", raising=False)
    assert load().poll_seconds == 20
