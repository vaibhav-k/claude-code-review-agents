"""
Record/replay support for testing against Microsoft Foundry without
spending a real API call on every run.

Both consumers of this module -- the .claude/agents/*.md fixture
validation harness (scripts/validate_fixtures.py, at the repo root) and
cli/'s own opt-in live integration test (test_cli_integration_live.py) --
need the same thing: a `Reviewer` (see agent_review.agents_client.Reviewer)
that, by default, replays a previously-recorded response instead of
calling a real model -- deterministic and free, safe to run on every PR --
but can be switched into "record" mode to hit a live Foundry resource once
and save what it returns for everyone else's replay.

Cassette entries are keyed by a hash of the exact (system_prompt,
user_message) pair, not by a human-assigned name. That is a *safety*
property, not just a convenience: if an agent's prompt (or a fixture file)
changes, its hash changes, so a stale recorded response can never silently
satisfy a new request. CassetteReviewer raises CassetteMissError instead
of guessing -- a cassette that quietly kept answering for a prompt that no
longer exists would defeat the entire point of a harness meant to catch
prompt regressions.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Protocol


class Reviewer(Protocol):
    def complete(self, system_prompt: str, user_message: str) -> str:
        """Structurally the same protocol as agent_review.agents_client.Reviewer
        -- redeclared here rather than imported so this test-support module
        has no dependency on the installed package's internals, only on the
        one method every real/fake/cassette reviewer in this project shares.
        """
        ...


class CassetteMissError(RuntimeError):
    """Raised in replay mode when no recorded response matches the exact
    (system_prompt, user_message) pair requested: either a brand-new case
    that has never been recorded, or a prompt/fixture change that has made
    a previously-recorded response stale. Either way, silently falling
    through would hide exactly the regression this harness exists to catch.
    """


def request_key(system_prompt: str, user_message: str) -> str:
    """Stable content hash identifying one (system_prompt, user_message)
    request. Deliberately not based on any human-assigned case name --
    see the module docstring for why that matters.
    """
    digest = hashlib.sha256()
    digest.update(system_prompt.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(user_message.encode("utf-8"))
    return digest.hexdigest()


_FILE_LINE_RE = re.compile(r"^Files?:\s*(?P<path>.+)$", re.MULTILINE)


def _label_for(user_message: str) -> str:
    """Best-effort human-readable label for a cassette entry, extracted
    from the conventional "File: <path>" (or "Files: a, b" for a
    multi-file case) first line every reviewer call in this project sends
    -- see orchestrator.py's _review_one_file and
    scripts/validate_fixtures.py's build_case_diff. Purely cosmetic --
    never used for lookup -- so a message that doesn't match this shape
    just gets a generic label instead of failing.
    """
    match = _FILE_LINE_RE.search(user_message)
    return match.group("path").strip() if match else "(unlabeled request)"


class Cassette:
    """Loads/saves the JSON file backing both CassetteReviewer (replay)
    and RecordingReviewer (record). Safe to construct against a path that
    doesn't exist yet -- it just starts empty.
    """

    def __init__(self, path: Path):
        self.path = path
        self._entries: dict[str, dict[str, str]] = {}
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            # "_meta" is a free-text human note (e.g. "seeded from
            # EXPECTED.md, not live-recorded" -- see
            # scripts/validate_fixtures.py's --seed-placeholders-from-expected),
            # never a request key -- excluded from the lookup table itself.
            self._entries = {k: v for k, v in raw.items() if k != "_meta"}
            self._meta = raw.get("_meta")
        else:
            self._meta = None

    def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        return entry["response"] if entry else None

    def put(self, key: str, label: str, response: str) -> None:
        self._entries[key] = {"label": label, "response": response}

    @property
    def meta(self) -> str | None:
        return self._meta

    def set_meta(self, note: str | None) -> None:
        self._meta = note

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = dict(self._entries)
        if self._meta:
            payload["_meta"] = self._meta
        # sort_keys so re-recording produces a minimal, reviewable git
        # diff (only genuinely new/changed entries move) rather than
        # reshuffling the whole file on every save.
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def __len__(self) -> int:
        return len(self._entries)


class CassetteReviewer:
    """Strict replay: every call must match a recorded entry exactly, or
    this raises CassetteMissError rather than guessing, going live, or
    returning a generic default.
    """

    def __init__(self, cassette: Cassette):
        self._cassette = cassette

    def complete(self, system_prompt: str, user_message: str) -> str:
        key = request_key(system_prompt, user_message)
        response = self._cassette.get(key)
        if response is None:
            raise CassetteMissError(
                f"No recorded response for {_label_for(user_message)!r} "
                f"(request hash {key[:12]}...) in {self._cassette.path}. "
                "This is either a case that has never been recorded, or a "
                "prompt/fixture changed since the cassette was last "
                "refreshed -- re-record against a live Foundry resource "
                "and commit the updated cassette."
            )
        return response


class RecordingReviewer:
    """Wraps a real Reviewer: calls it for real, saves the response into
    the cassette (keyed the same way CassetteReviewer looks entries up),
    and returns the live response unchanged. The caller is responsible
    for calling `cassette.save()` once recording is done (typically at
    test-session teardown) -- this class only mutates the in-memory
    Cassette, so a crash mid-run doesn't half-overwrite the file on disk.
    """

    def __init__(self, cassette: Cassette, live_reviewer: Reviewer):
        self._cassette = cassette
        self._live = live_reviewer

    def complete(self, system_prompt: str, user_message: str) -> str:
        response = self._live.complete(system_prompt, user_message)
        key = request_key(system_prompt, user_message)
        self._cassette.put(key, _label_for(user_message), response)
        return response
