"""Gemini error policy: auth/permission/not-found crash; only transient falls back."""
import pytest

from kitchen_prep.gemini.client import GeminiUnavailable, classify_model_error


class FakeAPIError(Exception):
    """Mimics a google-genai APIError carrying an HTTP status in ``.code``."""

    def __init__(self, code: int, message: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message


class FakeNetworkError(Exception):
    """Named like an httpx transport error."""


# Give it the recognised transport-error name.
FakeNetworkError.__name__ = "ConnectError"


def test_api_key_invalid_is_not_hidden():
    err = FakeAPIError(400, "API_KEY_INVALID")
    with pytest.raises(FakeAPIError):  # must surface, NOT become GeminiUnavailable
        classify_model_error(err)


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_auth_and_bad_request_codes_crash(code):
    with pytest.raises(FakeAPIError):
        classify_model_error(FakeAPIError(code, "fatal"))


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_transient_codes_become_unavailable(code):
    with pytest.raises(GeminiUnavailable):
        classify_model_error(FakeAPIError(code, "transient"))


def test_network_error_becomes_unavailable():
    with pytest.raises(GeminiUnavailable):
        classify_model_error(FakeNetworkError("connection reset"))


def test_unknown_programming_error_crashes():
    # No status code, not a network error -> must propagate unchanged.
    with pytest.raises(KeyError):
        classify_model_error(KeyError("bug in glue code"))
