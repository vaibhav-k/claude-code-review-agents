import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_review.agents_client import DEFAULT_MODEL, AnthropicFoundryReviewer


class _FakeConnectionError(Exception):
    """Duck-types `anthropic.APIConnectionError` closely enough for
    `complete()`'s error-enrichment check: carries `.request` (with a
    `.url`) but no `.response`. Deliberately not a real
    anthropic/httpx(2) instance -- which concrete httpx variant
    `anthropic` vendors has already changed once across versions this
    project supports (`anthropic>=0.74.0`), and complete() itself only
    ever duck-types on these two attributes, never on a specific
    exception class, so a plain fake exercises the exact same code path
    without coupling the test to today's installed version's internals.
    """

    def __init__(self, message: str, url: str):
        super().__init__(message)
        self.request = SimpleNamespace(url=url)


class _FakeStatusError(Exception):
    """Duck-types `anthropic.APIStatusError`: carries both `.request` and
    `.response`, which `complete()` must treat as already-informative and
    leave untouched (propagate as-is, not rewrap)."""

    def __init__(self, message: str, url: str):
        super().__init__(message)
        self.request = SimpleNamespace(url=url)
        self.response = SimpleNamespace(status_code=401)


_ENV_VARS = (
    "ANTHROPIC_FOUNDRY_RESOURCE",
    "ANTHROPIC_FOUNDRY_API_KEY",
    "ANTHROPIC_FOUNDRY_USE_ENTRA_ID",
    "ANTHROPIC_FOUNDRY_MODEL",
)


def _clear_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_missing_resource_raises_before_anything_else(monkeypatch: pytest.MonkeyPatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    with pytest.raises(RuntimeError, match="No Microsoft Foundry resource configured"):
        AnthropicFoundryReviewer()


def test_missing_api_key_raises_when_not_using_entra_id(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    with pytest.raises(RuntimeError, match="No Microsoft Foundry API key found"):
        AnthropicFoundryReviewer()


def test_constructs_with_resource_and_api_key(monkeypatch: pytest.MonkeyPatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    reviewer = AnthropicFoundryReviewer()
    assert reviewer._model == DEFAULT_MODEL


def test_explicit_kwargs_override_environment(monkeypatch: pytest.MonkeyPatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "env-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "env-key")
    reviewer = AnthropicFoundryReviewer(
        api_key="explicit-key", resource="explicit-resource", model="claude-opus-5"
    )
    assert reviewer._model == "claude-opus-5"


def test_use_entra_id_true_skips_the_api_key_check(monkeypatch: pytest.MonkeyPatch):
    # No API key configured at all -- Entra ID auth must not require one.
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    reviewer = AnthropicFoundryReviewer(use_entra_id=True)
    assert reviewer._model == DEFAULT_MODEL


def test_use_entra_id_env_var_is_equivalent_to_the_flag(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_USE_ENTRA_ID", "1")
    reviewer = AnthropicFoundryReviewer()
    assert reviewer._model == DEFAULT_MODEL


@pytest.mark.parametrize("falsy_value", ["0", "false", "False", "no", "off", ""])
def test_use_entra_id_env_var_falsy_string_does_not_enable_entra_id(
    monkeypatch: pytest.MonkeyPatch, falsy_value: str
):
    # Regression test: `bool(os.environ.get(...))` treats ANY non-empty string
    # as truthy, so ANTHROPIC_FOUNDRY_USE_ENTRA_ID=0 -- someone explicitly
    # trying to turn it OFF -- used to still enable Entra ID auth and skip
    # the API-key check entirely. With no API key configured, this must
    # raise the API-key error, not construct successfully via Entra ID.
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_USE_ENTRA_ID", falsy_value)
    with pytest.raises(RuntimeError, match="No Microsoft Foundry API key found"):
        AnthropicFoundryReviewer()


def test_use_entra_id_env_var_truthy_string_still_enables_entra_id(
    monkeypatch: pytest.MonkeyPatch,
):
    # Companion case: a genuinely truthy value must still work (guards
    # against an overcorrection that makes _env_flag too strict).
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_USE_ENTRA_ID", "true")
    reviewer = AnthropicFoundryReviewer()
    assert reviewer._model == DEFAULT_MODEL


def test_resource_and_api_key_from_env_are_stripped_of_whitespace(
    monkeypatch: pytest.MonkeyPatch,
):
    # Regression test: a resource name or key copied from a browser, a
    # .env file, or a PowerShell here-string commonly picks up a trailing
    # newline or stray space. Confirmed directly against the anthropic
    # SDK that an unstripped value flows straight through -- a trailing
    # space in `resource` gets URL-encoded into the hostname itself
    # (https://my-resource%20%20.services.ai.azure.com/...), and an
    # unstripped key is sent byte-for-byte in the Authorization header --
    # both produce an opaque connection/401 error indistinguishable from
    # a genuinely wrong credential. Must be stripped before reaching the
    # SDK either way.
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "  my-resource  \n")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "  my-key  \n")
    reviewer = AnthropicFoundryReviewer()
    assert reviewer._client.api_key == "my-key"
    assert reviewer._client.base_url == "https://my-resource.services.ai.azure.com/anthropic/"


def test_resource_and_api_key_kwargs_are_also_stripped_of_whitespace(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_env(monkeypatch)
    reviewer = AnthropicFoundryReviewer(resource="  my-resource  ", api_key="  my-key  ")
    assert reviewer._client.api_key == "my-key"
    assert reviewer._client.base_url == "https://my-resource.services.ai.azure.com/anthropic/"


def test_whitespace_only_resource_is_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "   ")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    with pytest.raises(RuntimeError, match="No Microsoft Foundry resource configured"):
        AnthropicFoundryReviewer()


def test_whitespace_only_api_key_is_treated_as_missing(monkeypatch: pytest.MonkeyPatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "   ")
    with pytest.raises(RuntimeError, match="No Microsoft Foundry API key found"):
        AnthropicFoundryReviewer()


def test_connection_error_is_enriched_with_the_attempted_host_and_a_hint(
    monkeypatch: pytest.MonkeyPatch,
):
    # Real-world trigger: ANTHROPIC_FOUNDRY_RESOURCE set to a model
    # deployment name (e.g. "gpt-5.2-1") instead of the actual Foundry
    # resource name -- the resulting hostname doesn't exist, so the SDK
    # raises anthropic.APIConnectionError("Connection error.") with no
    # further detail. Confirm complete() re-raises with the attempted URL
    # and a resource-vs-deployment-name hint instead of that bare message.
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "gpt-5.2-1")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    reviewer = AnthropicFoundryReviewer()

    bad_url = "https://gpt-5.2-1.services.ai.azure.com/anthropic/v1/messages"
    connection_error = _FakeConnectionError("Connection error.", url=bad_url)

    def _raise_connection_error(*args, **kwargs):
        raise connection_error

    monkeypatch.setattr(reviewer._client.messages, "create", _raise_connection_error)

    with pytest.raises(RuntimeError, match=re.escape(bad_url)) as exc_info:
        reviewer.complete("system", "user")
    assert "deployment name" in str(exc_info.value)


def test_non_connection_errors_from_the_sdk_are_not_swallowed_or_rewrapped(
    monkeypatch: pytest.MonkeyPatch,
):
    # Only the specific "no HTTP response at all" case should be
    # rewrapped -- an error that already carries an HTTP response (e.g. a
    # 401 from a resource that DOES resolve) must propagate unchanged, so
    # cli.py's existing "error: review failed: {exc}" still shows the
    # SDK's own, already-informative message.
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    reviewer = AnthropicFoundryReviewer()

    status_error = _FakeStatusError(
        "Access denied due to invalid subscription key or wrong API endpoint.",
        url="https://my-resource.services.ai.azure.com/anthropic/v1/messages",
    )

    def _raise_status_error(*args, **kwargs):
        raise status_error

    monkeypatch.setattr(reviewer._client.messages, "create", _raise_status_error)

    with pytest.raises(_FakeStatusError):
        reviewer.complete("system", "user")


def test_complete_joins_only_text_blocks_from_the_response(
    monkeypatch: pytest.MonkeyPatch,
):
    # Every other test here only ever exercises complete()'s exception
    # paths (connection-error enrichment, pass-through) -- this covers the
    # actual successful-call join/filter logic, so a regression that loses
    # the `block.type == "text"` guard (and starts accessing `.text` on a
    # non-text block, or concatenating one) would be caught here instead
    # of shipping untested.
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    reviewer = AnthropicFoundryReviewer()

    fake_response = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="first "),
            # Deliberately has no `.text` attribute at all: if the
            # `if block.type == "text"` guard is ever dropped, accessing
            # `.text` on this block raises AttributeError instead of
            # silently passing.
            SimpleNamespace(type="tool_use"),
            SimpleNamespace(type="text", text="second"),
        ]
    )
    monkeypatch.setattr(reviewer._client.messages, "create", lambda *args, **kwargs: fake_response)

    assert reviewer.complete("system", "user") == "first second"


def test_model_falls_back_to_default_when_nothing_else_is_set(
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    reviewer = AnthropicFoundryReviewer()
    assert reviewer._model == DEFAULT_MODEL


def test_model_env_var_is_used_when_no_explicit_model_is_passed(
    monkeypatch: pytest.MonkeyPatch,
):
    # The actual feature request this covers: set it once via env/.env
    # instead of passing --model on every invocation.
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_MODEL", "claude-haiku-4-5")
    reviewer = AnthropicFoundryReviewer()
    assert reviewer._model == "claude-haiku-4-5"


def test_explicit_model_kwarg_overrides_the_env_var(monkeypatch: pytest.MonkeyPatch):
    # Matches --resource/--use-entra-id precedence: an explicit flag
    # always wins over the environment variable.
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_MODEL", "claude-haiku-4-5")
    reviewer = AnthropicFoundryReviewer(model="claude-opus-5")
    assert reviewer._model == "claude-opus-5"


def test_model_env_var_is_stripped_of_whitespace(monkeypatch: pytest.MonkeyPatch):
    # Same whitespace hazard as resource/api_key: a deployment name
    # copied from the Foundry portal or a .env file commonly picks up a
    # trailing newline or stray space.
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_MODEL", "  claude-haiku-4-5  \n")
    reviewer = AnthropicFoundryReviewer()
    assert reviewer._model == "claude-haiku-4-5"


def test_empty_model_env_var_falls_back_to_default(monkeypatch: pytest.MonkeyPatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "my-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "fake-key")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_MODEL", "   ")
    reviewer = AnthropicFoundryReviewer()
    assert reviewer._model == DEFAULT_MODEL
