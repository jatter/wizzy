"""One fenced structure call per delivery; no production provider is released yet.

prepare must be local-only and check provider readiness/rights before a paid fence.
invoke must honor the 30-second timeout and disable ALL SDK/HTTP retries. A local
operation key is not vendor idempotency. Exceptions after fence submission (even
before its commit acknowledgement) require reconciliation, never automatic retry.
"""
import re
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from wizzy.execution import InvalidMessage, envelope


@dataclass(frozen=True)
class ProviderIdentity:
    provider: str
    model: str
    model_version: str

    def __post_init__(self):
        for value, pattern in (
            (self.provider, r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"),
            (self.model, r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}"),
            (self.model_version, r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}"),
        ):
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise ValueError("Invalid provider identity")


@dataclass(frozen=True)
class ProviderResponse:
    # Only sanitized receipt fields are persisted by ExecutionStore. Raw vendor
    # bodies/errors/credentials must not escape the adapter or enter broker data.
    outcome: str
    provider_request_id: str = field(repr=False)
    cost_microyuan: int | None = field(repr=False)
    result: dict = field(repr=False)


class StructureProvider(Protocol):
    identity: ProviderIdentity

    def prepare(self, lease: dict) -> object:
        """Local-only preflight and private input loading; no external calls."""
        ...

    def invoke(self, prepared: object, *, run_id: UUID, operation_key: UUID,
               timeout_seconds: int) -> ProviderResponse:
        """One remote request, no SDK retries; unknown cost stays None."""
        ...


class GradingConsumer:
    def __init__(self, store, provider: StructureProvider | None):
        if provider is None:
            raise RuntimeError("No released grading provider configured")
        if not isinstance(provider.identity, ProviderIdentity):
            raise RuntimeError("Invalid provider identity")
        self.store = store
        self.provider = provider
        self.identity = provider.identity

    def process(self, message):
        """Return safe operational status only, never a child result or error body.

        released can mean retry_wait OR exhausted/deadletter; DB is authoritative.
        reconciliation_required is an operational return, not a new job DB state.
        """
        try:
            message = envelope(message)
        except InvalidMessage:
            return "invalid_message"
        try:
            lease = self.store.claim(message)
        except Exception:
            return "unavailable"
        if lease is None:
            return "ignored"

        fence_started = False
        try:
            prepared = self.provider.prepare(dict(lease))
            if not self.store.heartbeat(lease):
                return "stopped"
            # Set BEFORE entering begin_call: losing a commit acknowledgement is
            # ambiguous, not evidence that no durable permission was recorded.
            fence_started = True
            call = self.store.begin_call(
                lease, stage="structure", provider=self.identity.provider,
                model=self.identity.model, model_version=self.identity.model_version)
            if call is None:
                return "stopped"
            response = self.provider.invoke(
                prepared, run_id=call["run_id"], operation_key=call["operation_key"],
                timeout_seconds=30)
            # Record late billing even if authorization/lease expired during I/O.
            if not self.store.finish_call(
                lease, call, outcome=response.outcome,
                provider_request_id=response.provider_request_id,
                cost_microyuan=response.cost_microyuan):
                return "reconciliation_required"
            if response.cost_microyuan is None:
                return "reconciliation_required"
            if not self.store.heartbeat(lease):
                return "stopped"
            return "settled" if self.store.complete(lease, response.result) else "stopped"
        except Exception:
            # Do not log exceptions: SQL, SDK and validation exceptions can carry
            # private answers, image URLs or credentials. Recovery reads DB state.
            if fence_started:
                return "reconciliation_required"
            try:
                return "released" if self.store.release(lease) else "stopped"
            except Exception:
                return "unavailable"
