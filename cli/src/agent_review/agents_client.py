"""
Thin wrapper around Claude, called exclusively via Microsoft Foundry
(Azure AI Foundry) -- there is no direct-to-api.anthropic.com code path
in this CLI. This project uses Azure only.

Kept to one small `Reviewer` protocol so the orchestrator can be tested
against a fake implementation with no network access -- see
tests/test_orchestrator.py. `AnthropicFoundryReviewer` is the real
implementation used at runtime; nothing else in this package imports the
`anthropic` package directly.

Microsoft Foundry serves Claude through an Anthropic-compatible Messages
API, and the official `anthropic` Python package ships a dedicated client
class for it (`anthropic.AnthropicFoundry`, parallel to `AnthropicBedrock`/
`AnthropicVertex` for the other clouds) -- `messages.create()`'s signature
is identical to direct Anthropic use, so every prompt in this project
(CLAUDE.md plus the 7 specialist agents) needed zero changes to run
through Azure. Only auth, the endpoint, and the model name change.

The `anthropic>=0.74.0` floor is the release that added `AnthropicFoundry`,
and its constructor keyword surface (`resource=`, `api_key=`,
`azure_ad_token_provider=`) has been verified directly against 0.74.0
itself (not just the newer 1.4.0 installed while building this module) --
so the pin isn't just "first version that has the class," it's confirmed
to accept the exact keywords this file calls it with.
"""

from __future__ import annotations

import os
from typing import Protocol

# Must match a model actually GA in Microsoft Foundry's catalog, not just
# any Anthropic model name -- Foundry's catalog and api.anthropic.com's
# catalog aren't guaranteed to match.
DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024

_ENTRA_SCOPE = "https://ai.azure.com/.default"
_FALSY_ENV_VALUES = {"", "0", "false", "no", "off"}

# Declared once and reused at every lookup site and error message in this
# module, and imported by cli.py for its --help text, so the env var
# names can't drift between "what's actually read" and "what the user is
# told to set." Intentionally public (no leading underscore): cli.py is a
# real cross-module consumer, not just an internal implementation detail.
ENV_RESOURCE = "ANTHROPIC_FOUNDRY_RESOURCE"
ENV_API_KEY = "ANTHROPIC_FOUNDRY_API_KEY"
ENV_USE_ENTRA_ID = "ANTHROPIC_FOUNDRY_USE_ENTRA_ID"
ENV_MODEL = "ANTHROPIC_FOUNDRY_MODEL"


def _env_flag(name: str) -> bool:
    """Parse a boolean-ish environment variable properly -- `bool(os.environ.get(...))`
    treats ANY non-empty string as True, so `ANTHROPIC_FOUNDRY_USE_ENTRA_ID=0`
    (someone explicitly trying to turn it off) would otherwise still enable
    Entra ID. Unset is also false; anything else (e.g. "1", "true") is true.
    """
    return os.environ.get(name, "").strip().lower() not in _FALSY_ENV_VALUES


class Reviewer(Protocol):
    def complete(self, system_prompt: str, user_message: str) -> str:
        """Return the model's full text response for one specialist call."""
        ...


class AnthropicFoundryReviewer:
    """Real implementation: Claude via Microsoft Foundry. Used when
    actually talking to the API.

    Requires the `anthropic` package (>=0.74.0, when `AnthropicFoundry`
    was added) and a Foundry resource name, plus one of two auth modes:

    - API key (default): `ANTHROPIC_FOUNDRY_API_KEY` environment variable,
      or `api_key` passed explicitly.
    - Entra ID (Azure AD): pass `use_entra_id=True` (or set
      `ANTHROPIC_FOUNDRY_USE_ENTRA_ID=1`) to authenticate via
      `azure-identity`'s `DefaultAzureCredential` instead of a static key.
      Requires the optional `azure-identity` dependency.

    The model is resolved the same way: `model` passed explicitly, else
    the `ANTHROPIC_FOUNDRY_MODEL` environment variable, else
    `DEFAULT_MODEL`. Whichever value wins must be a model actually
    *deployed* under the target resource -- some Foundry models don't
    support "deploymentless inference" (calling a bare model ID with no
    matching deployment), which fails with a `DeploymentError`, not a
    generic auth/connection error.

    Everything is validated eagerly at construction time -- a missing
    package, resource, or credential fails immediately with a clear
    message instead of partway through a review (and, worse, instead of
    surfacing as a raw traceback from inside the orchestrator's thread
    pool on the first file it tries to review). The model isn't (and
    can't be, without an extra API call this project deliberately doesn't
    spend) validated against what's actually deployed -- a wrong model
    still only surfaces once a real review call is made.
    """

    def __init__(
        self,
        api_key: str | None = None,
        resource: str | None = None,
        model: str | None = None,
        use_entra_id: bool | None = None,
    ):
        try:
            from anthropic import AnthropicFoundry  # noqa: PLC0415 -- deliberately

            # deferred: makes a missing/too-old `anthropic` package a clean
            # RuntimeError instead of an import-time crash for every user of
            # this module, including tests that never construct
            # AnthropicFoundryReviewer at all.
        except ImportError as exc:
            raise RuntimeError(
                "The 'anthropic' package (>=0.74.0, for Microsoft Foundry "
                "support) is required to run a real review. Install it "
                "with: pip install -U anthropic"
            ) from exc

        # .strip(): a resource name or key copied from a browser, a .env
        # file, or a PowerShell here-string very commonly picks up a
        # trailing newline or stray space. Left in, it becomes part of
        # the request (the resource name is spliced straight into the
        # hostname; the key goes straight into the Authorization header),
        # producing an opaque connection or 401 error that looks
        # identical to a genuinely wrong credential -- strip it here so
        # that specific, very common mistake is eliminated up front.
        resolved_resource = (resource or os.environ.get(ENV_RESOURCE) or "").strip()
        if not resolved_resource:
            raise RuntimeError(
                f"No Microsoft Foundry resource configured. Set the "
                f"{ENV_RESOURCE} environment variable to your "
                "Foundry resource name (or pass resource= explicitly)."
            )

        if use_entra_id is None:
            use_entra_id = _env_flag(ENV_USE_ENTRA_ID)

        if use_entra_id:
            try:
                from azure.identity import (  # noqa: PLC0415 -- same reasoning
                    DefaultAzureCredential,  # as the anthropic import above:
                    get_bearer_token_provider,  # azure-identity stays optional,
                )  # needed only when Entra ID auth is actually used.
            except ImportError as exc:
                raise RuntimeError(
                    "Entra ID auth requires the 'azure-identity' package. "
                    "Install it with: pip install azure-identity"
                ) from exc
            token_provider = get_bearer_token_provider(DefaultAzureCredential(), _ENTRA_SCOPE)
            self._client = AnthropicFoundry(
                azure_ad_token_provider=token_provider, resource=resolved_resource
            )
        else:
            resolved_key = (api_key or os.environ.get(ENV_API_KEY) or "").strip()
            if not resolved_key:
                raise RuntimeError(
                    f"No Microsoft Foundry API key found. Set the "
                    f"{ENV_API_KEY} environment variable (or pass "
                    "api_key= explicitly), or set use_entra_id=True / "
                    f"{ENV_USE_ENTRA_ID}=1 to authenticate via "
                    "Entra ID instead."
                )
            self._client = AnthropicFoundry(api_key=resolved_key, resource=resolved_resource)

        # Same precedence and whitespace-stripping treatment as
        # resource/api_key above, for the same reasons (a deployment name
        # pasted from the Foundry portal picks up stray whitespace just
        # as easily as a resource name or key does). Each candidate is
        # stripped BEFORE the `or` fallback chain, not after: a
        # whitespace-only value is truthy to `or` (it's a non-empty
        # string), so stripping only the final result would let
        # ANTHROPIC_FOUNDRY_MODEL="   " win over DEFAULT_MODEL instead of
        # being treated as unset -- caught by
        # test_empty_model_env_var_falls_back_to_default.
        self._model = (
            (model or "").strip() or (os.environ.get(ENV_MODEL) or "").strip() or DEFAULT_MODEL
        )

    def complete(self, system_prompt: str, user_message: str) -> str:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
        except Exception as exc:
            # Duck-typed rather than `except anthropic.APIConnectionError`:
            # this module only imports `anthropic` deferred, inside
            # __init__ (see the module docstring), so there's no
            # top-level `anthropic` name here to catch by type without a
            # second import. A connection-level failure (bad host, DNS
            # failure, network unreachable) carries `.request` but never
            # `.response` (that only exists once a real HTTP response
            # came back, e.g. a 401) -- enrich exactly that case, since
            # the SDK's own message ("Connection error.") gives no hint
            # about *which* host it tried or *why*, and in practice that
            # host is wrong far more often than the network is actually
            # down: ANTHROPIC_FOUNDRY_RESOURCE holding a model deployment
            # name (e.g. "gpt-5.2-1") instead of the Foundry resource
            # name is a real, easy mistake -- Microsoft's own docs single
            # it out as the most common mix-up setting this up.
            request = getattr(exc, "request", None)
            if request is not None and not hasattr(exc, "response"):
                raise RuntimeError(
                    f"{exc} (tried to reach {request.url}). This usually means "
                    "ANTHROPIC_FOUNDRY_RESOURCE doesn't match an actual "
                    "Microsoft Foundry resource -- double check it's the "
                    "resource name from your Foundry deployment's endpoint URL "
                    "(https://<resource-name>.services.ai.azure.com/anthropic), "
                    "not a model deployment name (e.g. not something like "
                    "'gpt-5.2-1' or 'claude-sonnet-4-6' -- those are model "
                    "deployment names, used as --model/DEFAULT_MODEL, not "
                    "the resource)."
                ) from exc
            raise
        return "".join(block.text for block in response.content if block.type == "text")
