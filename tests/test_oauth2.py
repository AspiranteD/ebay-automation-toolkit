"""Tests for eBay OAuth 2.0 authentication module."""
import json
import os
import tempfile
import time
from unittest.mock import patch, MagicMock

import pytest

from src.auth.oauth2 import EbayAuth, SCOPES, TOKEN_EXPIRY_BUFFER


@pytest.fixture
def token_file(tmp_path):
    return str(tmp_path / ".ebay_tokens.json")


@pytest.fixture
def auth(token_file):
    return EbayAuth(
        client_id="test-client-id",
        client_secret="test-client-secret",
        ru_name="test-ru-name",
        token_file=token_file,
    )


class TestBasicAuth:
    def test_base64_encoding(self, auth):
        import base64
        result = auth._get_basic_auth()
        decoded = base64.b64decode(result).decode()
        assert decoded == "test-client-id:test-client-secret"


class TestTokenPersistence:
    def test_load_empty_when_no_file(self, auth):
        assert auth._load_tokens() == {}

    def test_save_and_load_roundtrip(self, auth, token_file):
        tokens = {
            "access_token": "at_123",
            "refresh_token": "rt_456",
            "expires_at": time.time() + 7200,
        }
        auth._save_tokens(tokens)
        loaded = auth._load_tokens()
        assert loaded["access_token"] == "at_123"
        assert loaded["refresh_token"] == "rt_456"

    def test_save_creates_file(self, auth, token_file):
        auth._save_tokens({"access_token": "test"})
        assert os.path.exists(token_file)
        with open(token_file) as f:
            data = json.load(f)
        assert data["access_token"] == "test"


class TestAuthorizationURL:
    def test_contains_client_id(self, auth):
        url = auth.get_authorization_url()
        assert "client_id=test-client-id" in url

    def test_contains_redirect_uri(self, auth):
        url = auth.get_authorization_url()
        assert "redirect_uri=test-ru-name" in url

    def test_contains_response_type_code(self, auth):
        url = auth.get_authorization_url()
        assert "response_type=code" in url

    def test_contains_scopes(self, auth):
        url = auth.get_authorization_url()
        for scope in SCOPES:
            assert scope.replace(":", "%3A").replace("/", "%2F") in url or scope in url

    def test_url_starts_with_ebay(self, auth):
        url = auth.get_authorization_url()
        assert url.startswith("https://auth.ebay.com/oauth2/authorize")


class TestExchangeCode:
    @patch("src.auth.oauth2.requests.post")
    def test_exchange_saves_tokens(self, mock_post, auth, token_file):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "new_at",
                "refresh_token": "new_rt",
                "expires_in": 7200,
                "refresh_token_expires_in": 47304000,
            },
        )
        mock_post.return_value.raise_for_status = MagicMock()

        tokens = auth.exchange_code("auth_code_123")
        assert tokens["access_token"] == "new_at"
        assert tokens["refresh_token"] == "new_rt"
        assert "obtained_at" in tokens
        assert os.path.exists(token_file)

    @patch("src.auth.oauth2.requests.post")
    def test_exchange_sets_expiry(self, mock_post, auth):
        now = time.time()
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "at",
                "expires_in": 3600,
                "refresh_token_expires_in": 47304000,
            },
        )
        mock_post.return_value.raise_for_status = MagicMock()

        tokens = auth.exchange_code("code")
        assert tokens["expires_at"] >= now + 3500
        assert tokens["expires_at"] <= now + 3700

    @patch("src.auth.oauth2.requests.post")
    def test_exchange_sends_correct_data(self, mock_post, auth):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "at", "expires_in": 7200},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        auth.exchange_code("my_code")
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["data"]["grant_type"] == "authorization_code"
        assert call_kwargs[1]["data"]["code"] == "my_code"
        assert call_kwargs[1]["data"]["redirect_uri"] == "test-ru-name"


class TestRefresh:
    @patch("src.auth.oauth2.requests.post")
    def test_refresh_updates_access_token(self, mock_post, auth, token_file):
        auth._save_tokens({"refresh_token": "rt_existing", "access_token": "old_at"})

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "refreshed_at", "expires_in": 7200},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        tokens = auth.refresh()
        assert tokens["access_token"] == "refreshed_at"
        assert "refreshed_at" in tokens  # timestamp field

    @patch("src.auth.oauth2.requests.post")
    def test_refresh_preserves_existing_rt_if_not_rotated(self, mock_post, auth):
        auth._save_tokens({"refresh_token": "original_rt"})

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "new_at", "expires_in": 7200},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        tokens = auth.refresh()
        assert tokens["refresh_token"] == "original_rt"

    @patch("src.auth.oauth2.requests.post")
    def test_refresh_rotates_rt_if_provided(self, mock_post, auth):
        auth._save_tokens({"refresh_token": "old_rt"})

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "new_at",
                "refresh_token": "rotated_rt",
                "expires_in": 7200,
            },
        )
        mock_post.return_value.raise_for_status = MagicMock()

        tokens = auth.refresh()
        assert tokens["refresh_token"] == "rotated_rt"

    def test_refresh_raises_without_rt(self, auth):
        with pytest.raises(ValueError, match="No refresh_token"):
            auth.refresh()

    @patch("src.auth.oauth2.requests.post")
    def test_refresh_with_explicit_rt(self, mock_post, auth):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "at", "expires_in": 7200},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        tokens = auth.refresh(refresh_token="explicit_rt")
        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["data"]["refresh_token"] == "explicit_rt"


class TestGetAccessToken:
    def test_returns_valid_token(self, auth):
        auth._save_tokens({
            "access_token": "valid_at",
            "expires_at": time.time() + 3600,
        })
        assert auth.get_access_token() == "valid_at"

    @patch("src.auth.oauth2.requests.post")
    def test_refreshes_expired_token(self, mock_post, auth):
        auth._save_tokens({
            "access_token": "expired_at",
            "refresh_token": "rt",
            "expires_at": time.time() - 100,
        })
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fresh_at", "expires_in": 7200},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        token = auth.get_access_token()
        assert token == "fresh_at"

    @patch("src.auth.oauth2.requests.post")
    def test_refreshes_near_expiry_token(self, mock_post, auth):
        auth._save_tokens({
            "access_token": "almost_expired",
            "refresh_token": "rt",
            "expires_at": time.time() + 200,  # within 5-min buffer
        })
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"access_token": "fresh", "expires_in": 7200},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        token = auth.get_access_token()
        assert token == "fresh"

    def test_raises_without_any_tokens(self, auth):
        with pytest.raises(ValueError, match="No tokens"):
            auth.get_access_token()


class TestStatus:
    def test_valid_tokens(self, auth):
        auth._save_tokens({
            "access_token": "at",
            "expires_at": time.time() + 3600,
            "refresh_token_expires_at": time.time() + 86400 * 30,
            "obtained_at": "2024-01-01T00:00:00",
        })
        status = auth.get_status()
        assert status["access_token_valid"] is True
        assert status["refresh_token_valid"] is True
        assert status["access_token_remaining_minutes"] > 0
        assert status["refresh_token_remaining_days"] > 0

    def test_expired_tokens(self, auth):
        auth._save_tokens({
            "access_token": "at",
            "expires_at": time.time() - 1000,
            "refresh_token_expires_at": time.time() - 1000,
        })
        status = auth.get_status()
        assert status["access_token_valid"] is False
        assert status["refresh_token_valid"] is False


class TestExtractCode:
    def test_extract_from_callback_url(self):
        url = "https://example.com/callback?code=AUTH_CODE_123&state=abc"
        code = EbayAuth.extract_code_from_url(url)
        assert code == "AUTH_CODE_123"

    def test_extract_raw_code(self):
        raw = "v%5E1.1%23i%5E1%23p%5E3%23f%5E0%23I%5E3%23r%5E1%23t%5EUl44"
        code = EbayAuth.extract_code_from_url(raw)
        assert code == raw

    def test_short_string_returns_none(self):
        assert EbayAuth.extract_code_from_url("short") is None

    def test_url_with_multiple_params(self):
        url = "https://example.com/cb?state=xyz&code=MY_CODE&expires_in=300"
        code = EbayAuth.extract_code_from_url(url)
        assert code == "MY_CODE"
