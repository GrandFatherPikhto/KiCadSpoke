#!/usr/bin/env python3
"""Tests for kicadstamp/cli_common.py — the single owner of CLI exit codes
(PlacerError→1 / ApiError→1 / Exception→2), shared by kicadstamp_cli.py's
main() and author.cli_main()."""
import logging

import pytest
from kipy.errors import ApiError, ApiStatusCode

from kicadstamp.cli_common import api_error_message, run_cli
from kicadstamp.exceptions import PlacerError, ValidationError


def _raise(exc):
    def _fn():
        raise exc
    return _fn


class TestRunCli:
    def test_success_returns_zero(self):
        assert run_cli(lambda: None) == 0

    def test_placer_error_returns_one_and_logs(self, caplog):
        with caplog.at_level(logging.ERROR):
            code = run_cli(_raise(PlacerError("boom")))
        assert code == 1
        assert "boom" in caplog.text

    def test_validation_error_returns_one(self):
        """ValidationError subclasses PlacerError — same exit code."""
        assert run_cli(_raise(ValidationError("bad"))) == 1

    def test_api_error_returns_one(self):
        assert run_cli(_raise(ApiError("nope", code=ApiStatusCode.AS_TIMEOUT))) == 1

    def test_api_error_as_busy_logs_dedicated_message(self, caplog):
        with caplog.at_level(logging.ERROR):
            code = run_cli(_raise(ApiError("nope", code=ApiStatusCode.AS_BUSY)))
        assert code == 1
        assert "KiCad is busy" in caplog.text

    def test_unexpected_exception_returns_two(self, caplog):
        with caplog.at_level(logging.ERROR):
            code = run_cli(_raise(RuntimeError("bug")))
        assert code == 2
        assert "Unexpected error" in caplog.text

    def test_system_exit_propagates_unchanged(self):
        """SystemExit (a BaseException) is not swallowed — argparse errors and
        deliberate aborts keep their own exit code."""
        with pytest.raises(SystemExit) as exc_info:
            run_cli(_raise(SystemExit(3)))
        assert exc_info.value.code == 3


class TestApiErrorMessage:
    def test_as_busy_gets_dedicated_explanation(self):
        msg = api_error_message(ApiError("nope", code=ApiStatusCode.AS_BUSY))
        assert "KiCad is busy" in msg
        assert "not modified" in msg

    def test_other_code_gets_generic_message(self):
        msg = api_error_message(ApiError("nope", code=ApiStatusCode.AS_TIMEOUT))
        assert "KiCad returned API error" in msg
