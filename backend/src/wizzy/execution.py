"""Execution leases and durable call/accounting boundaries; no provider adapter yet.

A committed call fence disables automatic execution recovery/release, even if a
process crashes before sending. Never treat an ambiguous paid call as pre-call.
"""
import json
import re
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text

from wizzy.results import ConservativeResult


class InvalidMessage(ValueError):
    def __init__(self):
        super().__init__("Invalid grading message")


def envelope(message):
    keys = {"event_id", "job_id", "source_revision", "deletion_epoch", "request_id"}
    if not isinstance(message, dict) or set(message) != keys:
        raise InvalidMessage()
    result = dict(message)
    for key in ("event_id", "job_id"):
        value = result[key]
        if not isinstance(value, (str, UUID)):
            raise InvalidMessage()
        try:
            result[key] = UUID(str(value))
        except ValueError:
            raise InvalidMessage() from None
    for key, low, high in (("source_revision", 1, 2**31 - 1), ("deletion_epoch", 0, 2**63 - 1)):
        value = result[key]
        if type(value) is not int or not low <= value <= high:
            raise InvalidMessage()
    request = result["request_id"]
    if not isinstance(request, str) or not 1 <= len(request) <= 128 or "\x00" in request:
        raise InvalidMessage()
    return result


def check_worker_role(engine):
    with engine.connect() as db:
        unsafe = db.execute(text("""SELECT rolsuper OR rolbypassrls OR rolcreatedb OR
            rolcreaterole OR rolinherit OR rolreplication OR
            EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) OR
            EXISTS(SELECT 1 FROM pg_class WHERE relowner=r.oid) OR
            EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner=r.oid) OR
            EXISTS(SELECT 1 FROM pg_database WHERE datdba=r.oid)
            FROM pg_roles r WHERE rolname=current_user""")).scalar_one()
        grants = db.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
            AND c.relkind IN ('r','p','v','m','f') AND (
                has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES') OR
                has_table_privilege(current_user,c.oid,'DELETE,TRUNCATE,TRIGGER')))""")).scalar_one()
        if unsafe or grants:
            raise RuntimeError("Use a separate restricted worker role without table privileges")


class ExecutionStore:
    def __init__(self, engine):
        self.engine = engine
        check_worker_role(engine)

    def claim(self, message):
        values = envelope(message)
        with self.engine.begin() as db:
            row = db.execute(text("""SELECT * FROM public.claim_execution(
                :event_id,:job_id,:source_revision,:deletion_epoch,:request_id)"""), values).mappings().first()
            return dict(row) if row else None

    def begin_call(self, lease, *, stage, provider, model, model_version):
        """Commit a one-shot send permission. None means DO NOT call the provider.

        operation_key is a local dedupe key, not evidence of vendor idempotency.
        A lost return/commit acknowledgement must be reconciled, never resent.
        """
        if stage not in {"ocr", "structure", "verify", "tutor"}:
            raise InvalidMessage()
        for value, pattern in ((provider, r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"),
                               (model, r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}"),
                               (model_version, r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")):
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise InvalidMessage()
        values = self._lease_values(lease) | {
            "stage": stage, "provider": provider, "model": model, "model_version": model_version}
        with self.engine.begin() as db:
            row = db.execute(text("""SELECT * FROM public.begin_model_call(
                :event_id,:job_id,:source_revision,:deletion_epoch,:lease_token,
                :stage,:provider,:model,:model_version)"""), values).mappings().first()
            return dict(row) if row else None

    def finish_call(self, lease, call, *, outcome, provider_request_id, cost_microyuan=None):
        """Record sanitized billing metadata, NOT a child result or quota settlement.

        Missing pricing/usage remains unknown, never implicitly zero. The original
        call token can record a late receipt even after cancellation or expiry.
        """
        if (outcome not in {"succeeded", "failed"}
                or not isinstance(provider_request_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", provider_request_id)
                or (cost_microyuan is not None and
                    (type(cost_microyuan) is not int or not 0 <= cost_microyuan < 2**63))):
            raise InvalidMessage()
        values = self._lease_values(lease) | {
            "run": call["run_id"], "operation": call["operation_key"], "outcome": outcome,
            "provider_request": provider_request_id, "cost": cost_microyuan}
        with self.engine.begin() as db:
            return db.execute(text("""SELECT public.finish_model_call(
                :event_id,:job_id,:source_revision,:deletion_epoch,:lease_token,
                :run,:operation,:outcome,:provider_request,:cost)"""), values).scalar_one()

    def complete(self, lease, result):
        """Atomically persist a conservative result and settle successful-page quota.

        False means stale/conflicting execution or unfinished accounting. Never
        use it as permission to repeat a paid model call. Payloads stay internal.
        """
        try:
            ConservativeResult.model_validate(result)
            payload = json.dumps(result, allow_nan=False, ensure_ascii=False)
        except (ValidationError, ValueError, TypeError):
            raise InvalidMessage() from None
        values = self._lease_values(lease) | {"payload": payload}
        with self.engine.begin() as db:
            return db.execute(text("""SELECT public.complete_execution(
                :event_id,:job_id,:source_revision,:deletion_epoch,:lease_token,
                CAST(:payload AS jsonb))"""), values).scalar_one()

    @staticmethod
    def _lease_values(lease):
        return {key: lease[key] for key in (
            "event_id", "job_id", "source_revision", "deletion_epoch", "lease_token")}

    def heartbeat(self, lease):
        return self._settle("heartbeat_execution", lease)

    def release(self, lease):
        """Known pre-provider interruption; never use for an ambiguous paid call."""
        return self._settle("release_execution", lease)

    def _settle(self, function, lease):
        # Function names are internal constants, not message data. The database
        # independently reloads all authorization and matches token/version/epoch.
        values = self._lease_values(lease)
        with self.engine.begin() as db:
            return db.execute(text(f"""SELECT public.{function}(
                :event_id,:job_id,:source_revision,:deletion_epoch,:lease_token)"""), values).scalar_one()
