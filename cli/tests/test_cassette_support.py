"""
Unit tests for the record/replay cassette support code itself (not the
harnesses that use it) -- support/ is test infrastructure, but infrastructure
that silently misbehaves would make every test built on top of it
untrustworthy, so it gets the same direct test coverage as production code.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from support.cassette import (
    Cassette,
    CassetteMissError,
    CassetteReviewer,
    RecordingReviewer,
    request_key,
)


class _FakeLiveReviewer:
    """Records every call it receives, and returns a canned response --
    stands in for a real AnthropicFoundryReviewer without any network
    access, so RecordingReviewer's own behavior can be tested in isolation.
    """

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_message: str) -> str:
        self.calls.append((system_prompt, user_message))
        return self.response


def test_request_key_is_stable_and_order_sensitive():
    a = request_key("system", "user")
    b = request_key("system", "user")
    assert a == b
    # Swapping which string is which must not collide -- a naive
    # "system + user" concatenation would let ("ab", "c") and ("a", "bc")
    # hash identically.
    assert request_key("ab", "c") != request_key("a", "bc")


def test_cassette_replay_miss_raises_cassette_miss_error(tmp_path):
    cassette = Cassette(tmp_path / "empty.json")
    reviewer = CassetteReviewer(cassette)
    with pytest.raises(CassetteMissError, match="No recorded response"):
        reviewer.complete("sys", "File: foo.py\n\nsomething")


def test_recording_then_replaying_round_trips(tmp_path):
    cassette_path = tmp_path / "cassette.json"
    cassette = Cassette(cassette_path)
    live = _FakeLiveReviewer("[HIGH] foo.py:1 - x\nImpact: y\nFix: z")
    recorder = RecordingReviewer(cassette, live)

    response = recorder.complete("sys-prompt", "File: foo.py\n\ndiff here")
    assert response == live.response
    assert live.calls == [("sys-prompt", "File: foo.py\n\ndiff here")]

    cassette.save()
    assert cassette_path.exists()

    # A fresh Cassette instance loaded from disk (simulating a later,
    # separate replay-only run) must reproduce the exact recorded response.
    replayed = CassetteReviewer(Cassette(cassette_path))
    assert replayed.complete("sys-prompt", "File: foo.py\n\ndiff here") == live.response


def test_replay_is_strict_about_prompt_drift(tmp_path):
    # This is the core safety property: recording against one system
    # prompt, then replaying against a *different* one (e.g. because the
    # agent's .md file changed since the cassette was recorded) must miss,
    # not silently return the stale response.
    cassette_path = tmp_path / "cassette.json"
    cassette = Cassette(cassette_path)
    live = _FakeLiveReviewer("No high-impact issues found.")
    RecordingReviewer(cassette, live).complete("old prompt v1", "File: foo.py\n\ndiff")
    cassette.save()

    replayer = CassetteReviewer(Cassette(cassette_path))
    with pytest.raises(CassetteMissError):
        replayer.complete("new prompt v2 -- something changed", "File: foo.py\n\ndiff")


def test_cassette_save_round_trips_meta_note(tmp_path):
    cassette_path = tmp_path / "cassette.json"
    cassette = Cassette(cassette_path)
    cassette.set_meta("seeded from EXPECTED.md, not live-recorded")
    cassette.put(request_key("s", "u"), "some/file.py", "No high-impact issues found.")
    cassette.save()

    reloaded = Cassette(cassette_path)
    assert reloaded.meta == "seeded from EXPECTED.md, not live-recorded"
    assert reloaded.get(request_key("s", "u")) == "No high-impact issues found."
    # The meta note must not itself be mistaken for a request-key entry.
    assert len(reloaded) == 1


def test_cassette_len_reflects_recorded_entries(tmp_path):
    cassette = Cassette(tmp_path / "cassette.json")
    assert len(cassette) == 0
    cassette.put(request_key("s1", "u1"), "a.py", "No high-impact issues found.")
    cassette.put(request_key("s2", "u2"), "b.py", "No high-impact issues found.")
    assert len(cassette) == 2
