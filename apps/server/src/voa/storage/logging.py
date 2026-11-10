"""Allowlisted single-line JSON logging (DAT-02, SPEC 8.2)."""

import json
import logging

_STRING_FIELDS = (
    "event",
    "session_id",
    "device_id",
    "stream_id",
    "observation_id",
    "scenario_id",
    "state",
    "alert_id",
    "status",
    "error_code",
)
_INTEGER_FIELDS = ("seq", "latency_us")


class JsonLogFormatter(logging.Formatter):
    """Serialize only explicit structured ``extra`` fields.

    Usage: logger.info("", extra={"event": "admission", "status": "accepted"}).
    Free-form messages, interpolation arguments, exceptions and stack traces
    are never formatted. Containers and arbitrary objects cannot be smuggled
    through scalar metadata fields, and no default=str fallback is used.
    """

    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, str | int | list[str]] = {}
        for key in _STRING_FIELDS:
            value = record.__dict__.get(key)
            if type(value) is str:
                fields[key] = value
        for key in _INTEGER_FIELDS:
            value = record.__dict__.get(key)
            if type(value) is int and value >= 0:
                fields[key] = value
        reasons = record.__dict__.get("reason_codes")
        if (
            reasons is not None
            and type(reasons) in (list, tuple)
            and all(type(reason) is str for reason in reasons)
        ):
            fields["reason_codes"] = list(reasons)
        return json.dumps(fields, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
