"""Tests for the Streamlit review queue (app.py).

Streamlit is an optional extra (`requirements-app.txt`), so these skip when it
isn't installed — the core clean-clone install must not be forced to carry a UI
framework to run the test suite.

`AppTest` executes the real script headlessly, so an exception in any tab is a
test failure rather than something a judge discovers on stage. That is the whole
point of testing a demo UI: the failure mode is public.
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("streamlit", reason="UI extra not installed (see requirements-app.txt)")

from streamlit.testing.v1 import AppTest

from recon.agent import FinalBucket

# AppTest resolves relative paths against *this* file, not the repo root.
APP = str(pathlib.Path(__file__).resolve().parent.parent / "app.py")
# The first run loads the embedding model; generous, and still bounded.
TIMEOUT = 300


@pytest.fixture(scope="module")
def app():
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    return at


def test_the_app_runs_without_raising(app):
    assert not app.exception, [e.message for e in app.exception]


def test_all_four_views_are_present(app):
    assert len(app.tabs) == 4


def test_headline_metrics_match_the_pipeline(app):
    """The UI must not drift from the numbers the pipeline actually produced."""
    values = {m.label: m.value for m in app.metric}
    assert values["Bucket accuracy"] == "100.0%"
    assert values["Match precision"] == "100.0%"
    assert values["Match recall"] == "78.4%"
    assert values["Reached the LLM"] == "112"


def test_every_bucket_is_shown_and_they_sum_to_the_corpus(app):
    frame = app.dataframe[0].value
    assert len(frame) == len(FinalBucket)
    assert frame["Records"].sum() == 680
    assert abs(frame["Share"].sum() - 1.0) < 1e-6


def test_the_queue_shows_every_escalation(app):
    assert any("112 records the agent refused to guess" in s.value for s in app.subheader)


def test_the_exceptions_view_shows_every_exception(app):
    assert any("48 exceptions" in s.value for s in app.subheader)


def test_each_view_offers_a_record_to_inspect(app):
    labels = [sb.label for sb in app.selectbox]
    assert "Inspect a record" in labels
    assert "Inspect an exception" in labels
    assert "Trace a record end to end" in labels


def test_the_ui_states_that_triage_is_not_written_back(app):
    """The one claim a demo UI must not let a viewer make on its behalf."""
    text = " ".join(c.value for c in app.caption)
    assert "never written back to any ledger" in text


def test_throughput_shown_excludes_the_model_load(app):
    """Regression: the app must warm the index outside the timed run.

    When it didn't, the UI reported roughly a quarter of the committed
    throughput and looked like it was contradicting the README.
    """
    shown = {m.label: m.value for m in app.metric}["Throughput"]
    assert int(shown.split()[0]) > 100, shown
