"""Card 16 — the enrichment client.

Optional, prose-only, and never a source of fact. Rules enforced here:

* The key comes from ``CASCADE_MAP_API_KEY``. This module never reads or sets
  ``ANTHROPIC_API_KEY`` -- that variable belongs to Claude Code's own billing,
  not to CASCADE-MAP's runtime model calls.
* With no key set, :attr:`EnrichmentClient.enabled` is False and every call
  degrades to a no-op that returns empty strings. Nothing else in the card
  depends on this being on.
* Every call is injected through a *transport* callable so tests never touch
  the network. The default transport (used only outside tests, never
  exercised by this module's own test suite) raises rather than silently
  doing nothing, so a missing transport cannot be mistaken for "disabled."
* Output is prose only. It is the caller's job (``docrecords.py``) to keep it
  in a field structurally separate from AST- and trace-derived facts and to
  never feed it back into the graph.
* Payloads sent to the model are restricted to what this module accepts as
  input: short, already-public-in-the-record identity strings (name,
  qualname, kind, signature, docstring). Never target source bodies, never
  blob contents. `docs/runtime_prompts/` is the authority for prompt content
  and escalation policy; at the time this module was written that directory
  did not exist, so no prompt file is loaded and no escalation to
  claude-sonnet-5 is implemented here -- see the card's final report.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping

API_KEY_ENV = "CASCADE_MAP_API_KEY"
"""The only environment variable this module reads for credentials."""

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-5"

_FORBIDDEN_ENV = "ANTHROPIC_API_KEY"
"""Never read. Named only so a reviewer can grep for the constraint."""

# Fields an identity payload may carry to the model. Nothing else is sent.
_ALLOWED_IDENTITY_FIELDS = (
    "name",
    "qualname",
    "kind",
    "module",
    "signature",
    "docstring",
)

Transport = Callable[[Mapping[str, str], str], str]
"""``(payload, model) -> prose``. Swapped for a stub in every test."""


class EnrichmentDisabled(RuntimeError):
    """Raised only if code calls the client without checking ``enabled`` first
    and no stub transport was supplied. Should never surface in a real run:
    callers must check ``enabled``."""


@dataclass(frozen=True, slots=True)
class EnrichmentResult:
    """Prose plus the label it must always carry. Never a fact."""

    prose: str
    model_id: str
    source: str = "MODEL_PROPOSED"


class EnrichmentClient:
    """Talks to a language model for prose only, never for facts.

    ``transport`` is required to actually call anything. Without one, the
    client still reports ``enabled`` correctly (from the key alone) but
    raises if asked to summarize, so a forgotten transport fails loudly in
    development rather than silently producing empty prose that looks like a
    disabled run.
    """

    def __init__(
        self,
        api_key: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
        self._transport = transport

    @property
    def enabled(self) -> bool:
        """True only when a non-empty ``CASCADE_MAP_API_KEY`` was found."""
        return bool(self._api_key)

    def summarize_element(
        self,
        identity: Mapping[str, object],
        model: str = HAIKU_MODEL,
    ) -> EnrichmentResult:
        """Return a one-shot prose summary of an element's identity facts.

        Sends only the fields named in ``_ALLOWED_IDENTITY_FIELDS``, stringified
        and truncated defensively; never the element's full source body, never
        blob content. Returns an empty, unlabelled-model result when disabled.
        """
        if not self.enabled:
            return EnrichmentResult(prose="", model_id="")
        if self._transport is None:
            raise EnrichmentDisabled(
                "enrichment is enabled (a key is set) but no transport was "
                "configured; tests and callers must inject a stub transport "
                "rather than let this reach the network"
            )
        payload = {
            field: str(identity[field])[:2000]
            for field in _ALLOWED_IDENTITY_FIELDS
            if field in identity and identity[field] not in (None, "")
        }
        prose = self._transport(payload, model)
        return EnrichmentResult(prose=prose, model_id=model)
