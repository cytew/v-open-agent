"""TASK-15 JSON log allowlist and leak prevention (DAT-02, SPEC 8.2)."""

import io
import json
import logging

import pytest

from voa.storage.logging import JsonLogFormatter


def captured_logger() -> tuple[logging.Logger, io.StringIO]:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.Logger("storage-test")
    logger.addHandler(handler)
    return logger, output


def test_all_and_only_allowed_fields_are_emitted() -> None:
    logger, output = captured_logger()
    fields = {
        "event": "admission",
        "session_id": "sess_1",
        "device_id": "dev_1",
        "stream_id": "camera_0",
        "seq": 0,
        "observation_id": "obs_1",
        "scenario_id": "visible_obstacle_in_corridor",
        "state": "unknown",
        "reason_codes": ["input_stale", "quality_blocked"],
        "alert_id": "alert_1",
        "status": "accepted",
        "latency_us": 0,
        "error_code": "unsupported_message_type",
    }
    logger.info("", extra=fields)
    assert json.loads(output.getvalue()) == fields


def test_unknown_fields_message_and_interpolation_are_not_logged() -> None:
    logger, output = captured_logger()
    secret = "private-token-raw-media-and-transcript"
    logger.info(
        f"{secret}: %s",
        secret,
        extra={
            "event": "admission",
            "token": secret,
            "secret": secret,
            "payload": {"text": secret},
            "text": secret,
            "raw_bytes": secret.encode(),
            "media_path": secret,
        },
    )
    assert json.loads(output.getvalue()) == {"event": "admission"}
    assert secret not in output.getvalue()


def test_exceptions_and_stack_traces_are_not_appended() -> None:
    logger, output = captured_logger()
    secret = "private-exception-payload"
    try:
        raise ValueError(secret)
    except ValueError:
        logger.exception(secret, stack_info=True, extra={"event": "failure", "error_code": "io"})
    assert json.loads(output.getvalue()) == {"event": "failure", "error_code": "io"}
    assert secret not in output.getvalue()
    assert "Traceback" not in output.getvalue()
    assert output.getvalue().count("\n") == 1


def test_missing_metadata_produces_empty_json_without_using_message() -> None:
    logger, output = captured_logger()
    logger.warning({"token": "private-message"})
    logger.info("private-message")
    assert output.getvalue().splitlines() == ["{}", "{}"]


def test_json_escapes_newlines_and_preserves_unicode_in_one_line() -> None:
    logger, output = captured_logger()
    event = "관측\n수신\r완료"
    logger.info("", extra={"event": event})
    assert json.loads(output.getvalue()) == {"event": event}
    assert output.getvalue().count("\n") == 1


@pytest.mark.parametrize(
    "value",
    [{"token": "private"}, ["private"], b"private", None, 1, float("nan")],
)
def test_scalar_fields_cannot_carry_containers_bytes_or_other_types(value: object) -> None:
    logger, output = captured_logger()
    logger.info("", extra={"event": value, "session_id": value})
    assert output.getvalue().strip() == "{}"


@pytest.mark.parametrize("value", [-1, True, "123", b"123", float("nan"), {"token": "private"}])
def test_integer_fields_reject_invalid_values(value: object) -> None:
    logger, output = captured_logger()
    logger.info("", extra={"seq": value, "latency_us": value})
    assert output.getvalue().strip() == "{}"


@pytest.mark.parametrize(
    "value", ["private", {"token": "private"}, [{"token": "private"}], [b"private"], [None]]
)
def test_reason_codes_cannot_carry_nested_or_unstructured_payloads(value: object) -> None:
    logger, output = captured_logger()
    logger.info("", extra={"reason_codes": value})
    assert output.getvalue().strip() == "{}"


def test_formatter_does_not_stringify_unknown_objects() -> None:
    class Unprintable:
        def __str__(self) -> str:
            raise AssertionError("must not format arbitrary objects")

    logger, output = captured_logger()
    logger.info(Unprintable(), extra={"status": Unprintable(), "payload": Unprintable()})
    assert output.getvalue().strip() == "{}"
