"""Guardrails for the AI tools served over MCP.

``guarded_tool`` declares an ``ai_tool`` tool and wraps it so that every call:

* validates its arguments against the declared input schema. Unknown
  arguments are refused, so a client can never smuggle a domain or raw SQL;
* runs under a statement timeout;
* for read tools, runs inside a savepoint switched to a read-only transaction
  that is always rolled back, so an accidental write fails instead of
  persisting;
* for write tools, requires a two-step confirmation. The first call
  (``confirm=false``) returns a preview and a signed token bound to the user,
  the tool, the arguments and the data the preview saw. The second call must
  present the same arguments and the token, and aborts if anything changed;
* returns a common envelope (environment, period, definitions, data quality
  warnings and generation time) around the tool result;
* turns unexpected errors into plain messages, without tracebacks.

A read tool returns a dict with ``result`` (a dict) and, optionally,
``summary``, ``period``, ``definitions`` and ``warnings``. A write tool is the
method that builds the preview: it returns the same keys plus, optionally,
``binding`` (what the token signs; the preview ``result`` by default) and
``plan`` (what the apply method receives; the preview ``result`` by
default). The apply method is named in the decorator and receives the plan
plus the validated arguments.
"""

import functools
import hmac as hmac_lib
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone

from psycopg2 import errors as pg_errors

from odoo import _
from odoo.exceptions import UserError
from odoo.tools.misc import hmac as odoo_hmac

from odoo.addons.ai_tool.tools import aitool

_logger = logging.getLogger(__name__)

GUARD_ATTR = "_pms_ai_guard"
TOOL_CONTEXT_KEY = "pms_ai_tool_id"
CONFIRM_SCOPE = "pms_ai_tool.confirm"

PARAM_ENVIRONMENT = "pms_ai_tool.environment"
PARAM_TIMEOUT = "pms_ai_tool.statement_timeout_ms"
PARAM_CONFIRMATION_TTL = "pms_ai_tool.confirmation_ttl"
PARAM_WRITE_ENABLED = "pms_ai_tool.write_enabled"

DEFAULT_TIMEOUT_MS = 15000
DEFAULT_CONFIRMATION_TTL = 600

SUPPORTED_TYPES = {"string", "integer", "number", "boolean", "array"}
OUTPUT_KEYS = {"result", "summary", "period", "definitions", "warnings"}
WRITE_OUTPUT_KEYS = OUTPUT_KEYS | {"binding", "plan"}

CONFIRM_PROPERTIES = {
    "confirm": {
        "type": "boolean",
        "description": "False (default) previews the change and returns a "
        "confirmation token. True applies it: repeat the exact same arguments "
        "and pass the token.",
    },
    "confirmation_token": {
        "type": "string",
        "maxLength": 200,
        "description": "Token returned by the preview call.",
    },
}

ENVELOPE_PROPERTIES = {
    "environment": {"type": "string"},
    "generated_at": {"type": "string"},
    "tool": {"type": "string"},
    "summary": {"type": "string"},
    "period": {"type": "object"},
    "definitions": {"type": "array", "items": {"type": "string"}},
    "warnings": {"type": "array", "items": {"type": "string"}},
}


def guarded_tool(
    input_schema,
    output_schema=None,
    required_inputs=None,
    write=False,
    apply=None,
    timeout_ms=None,
    log_result=True,
):
    """Declare an AI tool with the PMS guardrails.

    :param input_schema: JSON schema ``properties`` of the arguments. Strings
        need ``maxLength``, ``enum`` or ``format: date``; arrays need
        ``maxItems``. Free objects are not accepted.
    :param output_schema: JSON schema ``properties`` of ``result``.
    :param required_inputs: names of the mandatory arguments.
    :param write: whether the tool changes data (two-step confirmation).
    :param apply: name of the method that applies a confirmed write.
    :param timeout_ms: statement timeout, overriding the system parameter.
    :param log_result: False keeps only the envelope (no ``result``) in the
        MCP log, for tools that return personal data.
    """
    input_schema = dict(input_schema or {})
    required_inputs = list(required_inputs or [])
    _check_input_schema(input_schema, required_inputs)
    if write and not apply:
        raise ValueError("A write tool needs the name of its apply method")
    if apply and not write:
        raise ValueError("Only write tools have an apply method")
    if write:
        reserved = set(CONFIRM_PROPERTIES) & set(input_schema)
        if reserved:
            raise ValueError(f"Reserved argument names: {sorted(reserved)}")
    declared_schema = (
        dict(input_schema, **CONFIRM_PROPERTIES) if write else input_schema
    )
    envelope_schema = dict(
        ENVELOPE_PROPERTIES,
        result={"type": "object", "properties": output_schema or {}},
    )
    if write:
        envelope_schema["confirmation"] = {"type": "object"}
    guard = {
        "write": write,
        "apply": apply,
        "timeout_ms": timeout_ms,
        "log_result": log_result,
        "input_schema": input_schema,
        "required": required_inputs,
    }

    def decorator(func):
        if not func.__name__.startswith("_"):
            # Public methods are reachable through RPC, which would bypass
            # the per-tool access check done in ai.tool._execute_tool.
            raise ValueError(f"AI tool methods must be private: {func.__name__}")

        @functools.wraps(func)
        def wrapper(self, **kwargs):
            return _run_guarded(self, func, guard, kwargs)

        setattr(wrapper, GUARD_ATTR, guard)
        return aitool(declared_schema, envelope_schema, required_inputs)(wrapper)

    return decorator


def get_guard(func):
    """Return the guard settings of a tool method, or None if unguarded."""
    return getattr(func, GUARD_ATTR, None)


def truncate(text, limit=80):
    """Shorten free text (bank concepts, notes) before returning it."""
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# Schema checks and argument validation


def _check_input_schema(properties, required):
    for name, spec in properties.items():
        _check_property(name, spec)
    unknown = set(required) - set(properties)
    if unknown:
        raise ValueError(f"Required arguments not declared: {sorted(unknown)}")


def _check_property(name, spec):
    kind = spec.get("type")
    if kind not in SUPPORTED_TYPES:
        raise ValueError(f"Argument {name}: unsupported type {kind!r}")
    if kind == "string" and not (
        spec.get("maxLength") or spec.get("enum") or spec.get("format") == "date"
    ):
        raise ValueError(f"Argument {name}: strings need maxLength, enum or a date")
    if kind == "array":
        if not spec.get("maxItems"):
            raise ValueError(f"Argument {name}: arrays need maxItems")
        _check_property(f"{name}[]", spec.get("items") or {})


def _validate_arguments(guard, kwargs):
    properties = guard["input_schema"]
    unknown = sorted(set(kwargs) - set(properties))
    if unknown:
        raise UserError(
            _("Unknown arguments: %(unknown)s. Accepted: %(accepted)s.")
            % {
                "unknown": ", ".join(unknown),
                "accepted": ", ".join(sorted(properties)) or "-",
            }
        )
    cleaned = {}
    for name, value in kwargs.items():
        if value is None:
            continue
        cleaned[name] = _validate_value(name, value, properties[name])
    missing = [name for name in guard["required"] if name not in cleaned]
    if missing:
        raise UserError(_("Missing arguments: %s.") % ", ".join(missing))
    return cleaned


def _validate_value(name, value, spec):  # noqa: C901
    kind = spec["type"]
    if kind == "string":
        if not isinstance(value, str):
            raise UserError(_("%s must be a text.") % name)
        if spec.get("format") == "date":
            try:
                return date.fromisoformat(value)
            except ValueError:
                raise UserError(
                    _("%s must be a date in YYYY-MM-DD format.") % name
                ) from None
        if spec.get("maxLength") and len(value) > spec["maxLength"]:
            raise UserError(
                _("%(name)s is longer than %(limit)s characters.")
                % {"name": name, "limit": spec["maxLength"]}
            )
        if spec.get("pattern") and not re.fullmatch(spec["pattern"], value):
            raise UserError(_("%s has an invalid format.") % name)
    elif kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise UserError(_("%s must be an integer.") % name)
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise UserError(_("%s must be a number.") % name)
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise UserError(_("%s must be true or false.") % name)
    elif kind == "array":
        if not isinstance(value, list):
            raise UserError(_("%s must be a list.") % name)
        if len(value) > spec["maxItems"]:
            raise UserError(
                _("%(name)s accepts at most %(limit)s items.")
                % {"name": name, "limit": spec["maxItems"]}
            )
        if len(value) < spec.get("minItems", 0):
            raise UserError(
                _("%(name)s needs at least %(limit)s items.")
                % {"name": name, "limit": spec["minItems"]}
            )
        return [_validate_value(f"{name}[]", item, spec["items"]) for item in value]
    if "enum" in spec and value not in spec["enum"]:
        raise UserError(
            _("%(name)s must be one of: %(values)s.")
            % {"name": name, "values": ", ".join(map(str, spec["enum"]))}
        )
    if "minimum" in spec and value < spec["minimum"]:
        raise UserError(
            _("%(name)s must be at least %(limit)s.")
            % {"name": name, "limit": spec["minimum"]}
        )
    if "maximum" in spec and value > spec["maximum"]:
        raise UserError(
            _("%(name)s must be at most %(limit)s.")
            % {"name": name, "limit": spec["maximum"]}
        )
    return value


# Execution


def _run_guarded(model, func, guard, kwargs):
    env = model.env
    tool = _current_tool(env)
    label = tool.name or func.__name__
    confirm = token = None
    if guard["write"]:
        confirm = kwargs.pop("confirm", None)
        token = kwargs.pop("confirmation_token", None)
        if confirm is not None and not isinstance(confirm, bool):
            raise UserError(_("confirm must be true or false."))
    arguments = _validate_arguments(guard, kwargs)
    timeout_ms = guard["timeout_ms"] or int(
        env["ir.config_parameter"].sudo().get_param(PARAM_TIMEOUT, DEFAULT_TIMEOUT_MS)
    )
    try:
        with _guarded_transaction(env, True, timeout_ms):
            output = func(model, **arguments)
        if not guard["write"]:
            return _envelope(env, label, output, OUTPUT_KEYS)
        binding = output.pop("binding", output.get("result"))
        plan = output.pop("plan", output.get("result"))
        signature = _signature_payload(env, model, func, arguments, binding)
        if not confirm:
            response = _envelope(env, label, output, WRITE_OUTPUT_KEYS)
            response["confirmation"] = _make_confirmation(env, signature)
            if not _writes_enabled(env, tool):
                response["warnings"].append(
                    _("Changes are disabled for this tool: the confirmation will fail.")
                )
            return response
        if not _writes_enabled(env, tool):
            raise UserError(_("Changes are disabled for the tool %s.") % label)
        _verify_confirmation(env, token, signature)
        with _guarded_transaction(env, False, timeout_ms):
            # Spent with the change itself: if the change fails, the token
            # can be used again; once it succeeds, a replay is refused.
            env["pms.ai.confirmation"]._consume(token, label)
            applied = getattr(model, guard["apply"])(plan, **arguments)
        return _envelope(env, label, applied, OUTPUT_KEYS)
    except UserError:
        raise
    except pg_errors.QueryCanceled:
        raise UserError(
            _(
                "The query took longer than %s seconds and was cancelled. "
                "Narrow the period or the hotels and try again."
            )
            % (timeout_ms // 1000 or 1)
        ) from None
    except pg_errors.ReadOnlySqlTransaction:
        _logger.exception("AI tool %s tried to write", label)
        raise UserError(
            _("The tool %s tried to change data and was stopped; nothing was saved.")
            % label
        ) from None
    except Exception:
        _logger.exception("AI tool %s failed", label)
        raise UserError(
            _("Internal error in the tool %s. It has been logged.") % label
        ) from None


@contextmanager
def _guarded_transaction(env, read_only, timeout_ms):
    """Run a block in a savepoint with a statement timeout.

    Read-only blocks are always rolled back, which also restores the
    timeout. Write blocks are released, so the timeout is reset by hand.
    """
    cr = env.cr
    # Leave nothing pending for the read-only block: flushing some fields can
    # queue the recomputation of others, so flush until nothing is left.
    for _attempt in range(10):
        env.flush_all()
        if not env.fields_to_compute() and not env.cache.get_dirty_fields():
            break
    cr.execute("SHOW statement_timeout")
    previous_timeout = cr.fetchone()[0]
    savepoint = cr.savepoint()
    try:
        if read_only:
            cr.execute("SET LOCAL transaction_read_only = on")
        cr.execute(
            "SELECT set_config('statement_timeout', %s, true)", [str(int(timeout_ms))]
        )
        yield
        # Surface pending ORM writes while the guards are still in place.
        env.flush_all()
    except BaseException:
        savepoint.close(rollback=True)
        raise
    savepoint.close(rollback=read_only)
    if not read_only:
        cr.execute(
            "SELECT set_config('statement_timeout', %s, true)", [previous_timeout]
        )


def _current_tool(env):
    tool_id = env.context.get(TOOL_CONTEXT_KEY)
    return env["ai.tool"].sudo().browse(tool_id).exists() if tool_id else env["ai.tool"]


def _environment(env):
    return (
        env["ir.config_parameter"].sudo().get_param(PARAM_ENVIRONMENT)
        or os.environ.get("DOODBA_ENVIRONMENT")
        or "unknown"
    )


def _envelope(env, label, output, allowed_keys):
    if not isinstance(output, dict) or not isinstance(output.get("result"), dict):
        raise TypeError(f"AI tool {label} must return a dict with a dict 'result'")
    unexpected = set(output) - allowed_keys
    if unexpected:
        raise TypeError(f"AI tool {label} returned unexpected keys {unexpected}")
    return {
        "environment": _environment(env),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": label,
        "summary": output.get("summary") or "",
        "period": _jsonable(output.get("period") or {}),
        "definitions": [str(text) for text in output.get("definitions") or []],
        "warnings": [str(text) for text in output.get("warnings") or []],
        "result": _jsonable(output["result"]),
    }


def _jsonable(value):
    """Round-trip through JSON so dates, Decimals... reach the client as text."""
    return json.loads(json.dumps(value, default=str))


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# Two-step confirmation


def _writes_enabled(env, tool):
    enabled = env["ir.config_parameter"].sudo().get_param(PARAM_WRITE_ENABLED)
    return bool(tool) and tool.write_enabled and enabled in ("1", "True", "true")


def _signature_payload(env, model, func, arguments, binding):
    return _canonical(
        {
            "user": env.uid,
            "tool": f"{model._name}.{func.__name__}",
            "arguments": arguments,
            "binding": binding,
            # A copy of the database (staging) shares database.secret and the
            # data: bind the token to what the copy does not carry.
            "environment": os.environ.get("DOODBA_ENVIRONMENT", ""),
            "base_url": env["ir.config_parameter"].sudo().get_param("web.base.url"),
        }
    )


def _sign(env, signature, expires_at):
    return odoo_hmac(env(su=True), CONFIRM_SCOPE, f"{signature}|{expires_at}")


def _make_confirmation(env, signature):
    ttl = int(
        env["ir.config_parameter"]
        .sudo()
        .get_param(PARAM_CONFIRMATION_TTL, DEFAULT_CONFIRMATION_TTL)
    )
    expires_at = int(time.time()) + ttl
    return {
        "required": True,
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
        "instructions": _(
            "Show this preview to the user. Only if they approve it, call the "
            "tool again with exactly the same arguments plus confirm=true and "
            "this confirmation_token."
        ),
        "confirmation_token": f"{expires_at}.{_sign(env, signature, expires_at)}",
    }


def _verify_confirmation(env, token, signature):
    if not token:
        raise UserError(
            _("Missing confirmation_token: call the tool with confirm=false first.")
        )
    try:
        expires_str, received = token.split(".", 1)
        expires_at = int(expires_str)
    except ValueError:
        raise UserError(
            _("Invalid confirmation_token: preview the change again.")
        ) from None
    if time.time() > expires_at:
        raise UserError(_("The confirmation has expired: preview the change again."))
    expected = _sign(env, signature, expires_at)
    if not hmac_lib.compare_digest(received.encode(), expected.encode()):
        raise UserError(
            _(
                "The confirmation does not match: the arguments, the user or the "
                "data changed since the preview. Preview the change again."
            )
        )
