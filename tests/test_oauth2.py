"""Tests for the eBay OAuth2 authentication module."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.auth.oauth2 import (
    DEFAULT_SCOPES,
    EBAY_AUTH_URL,
    EBAY_TOKEN_URL,
    EbayOAuth2Client,
    TokenData,
)


@pytest.fixture
def client(tmp_path):
    token_file = tmp_path / "tokens.json"
    return EbayOAuth2Client(
        client_id="test_client_id",
        client_secret="test_client_secret",
        ru_name="test_runame",
        token_file=str(token_file),
    )


@pytest.fixture
def client_with_tokens(tmp_path):
    token_file = tmp_path / "tokens.json"
    tokens = {
        "access_token": "valid_access_token",
        "refresh_token": "valid_refresh_token",
        "expires_at": time.time() + 7200,
        "token_type": "Bearer",
    }
    token_file.write_text(json.dumps(tokens))
    return EbayOAuth2Client(
        client_id="test_client_id",
        client_secret="test_client_secret",
        ru_name="test_runame",
        token_file=str(token_file),
    )


class TestAuthorizationURL:
    def test_builds_correct_url(self, client):
        url = client.get_authorization_url()
        assert url.startswith(EBAY_AUTH_URL)
        assert "client_id=test_client_id" in url
        assert "redirect_uri=test_runame" in url
        assert "response_type=code" in url

    def test_includes_scopes(self, client):
        url = client.get_authorization_url()
        assert "scope=" in url
        assert "sell.inventory" in url
        assert "sell.fulfillment" in url

    def test_includes_state_parameter(self, client):
        url = client.get_authorization_url(state="random_state_123")
        assert "state=random_state_123" in url

    def test_no_state_when_empty(self, client):
        url = client.get_authorization_url()
        assert "state=" not in url


class TestTokenExchange:
    @patch("src.auth.oauth2.requests.post")
    def test_exchange_code_returns_token_data(self, mock_post, client):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
                "expires_in": 7200,
                "token_type": "Bearer",
            },
        )

        result = client.exchange_code_for_tokens("auth_code_123")

        assert result.access_token == "new_access_token"
        assert result.refresh_token == "new_refresh_token"
        assert isinstance(result, TokenData)

    @patch("src.auth.oauth2.requests.post")
    def test_exchange_code_sends_correct_payload(self, mock_post, client):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "tok",
                "refresh_token": "ref",
                "expires_in": 7200,
            },
        )

        client.exchange_code_for_tokens("my_code")

        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["data"]["grant_type"] == "authorization_code"
        assert call_kwargs[1]["data"]["code"] == "my_code"
        assert call_kwargs[1]["data"]["redirect_uri"] == "test_runame"

    @patch("src.auth.oauth2.requests.post")
    def test_exchange_saves_tokens_to_file(self, mock_post, client):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "tok",
                "refresh_token": "ref",
                "expires_in": 7200,
            },
        )

        client.exchange_code_for_tokens("code")

        assert client.token_file.exists()
        saved = json.loads(client.token_file.read_text())
        assert saved["access_token"] == "tok"


class TestTokenRefresh:
    @patch("src.auth.oauth2.requests.post")
    def test_refresh_updates_access_token(self, mock_post, client_with_tokens):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "refreshed_token",
                "expires_in": 7200,
                "token_type": "Bearer",
            },
        )

        result = client_with_tokens.refresh_access_token()

        assert result.access_token == "refreshed_token"
        assert result.refresh_token == "valid_refresh_token"  # preserved

    @patch("src.auth.oauth2.requests.post")
    def test_refresh_sends_correct_grant_type(self, mock_post, client_with_tokens):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "new",
                "expires_in": 7200,
            },
        )

        client_with_tokens.refresh_access_token()

        call_kwargs = mock_post.call_args
        assert call_kwargs[1]["data"]["grant_type"] == "refresh_token"

    def test_refresh_without_tokens_raises(self, client):
        with pytest.raises(ValueError, match="No refresh token"):
            client.refresh_access_token()


class TestGetValidToken:
    def test_returns_token_when_valid(self, client_with_tokens):
        token = client_with_tokens.get_valid_token()
        assert token == "valid_access_token"

    @patch("src.auth.oauth2.requests.post")
    def test_auto_refreshes_when_expired(self, mock_post, tmp_path):
        token_file = tmp_path / "tokens.json"
        tokens = {
            "access_token": "expired_token",
            "refresh_token": "valid_refresh",
            "expires_at": time.time() - 100,  # expired
        }
        token_file.write_text(json.dumps(tokens))

        client = EbayOAuth2Client(
            client_id="id",
            client_secret="secret",
            ru_name="ru",
            token_file=str(token_file),
        )

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "fresh_token",
                "expires_in": 7200,
            },
        )

        token = client.get_valid_token()
        assert token == "fresh_token"
        mock_post.assert_called_once()

    def test_raises_when_no_tokens(self, client):
        with pytest.raises(ValueError, match="No tokens available"):
            client.get_valid_token()


class TestBasicAuth:
    def test_basic_auth_encoding(self, client):
        import base64

        auth = client._build_basic_auth()
        expected = base64.b64encode(b"test_client_id:test_client_secret").decode()
        assert auth == f"Basic {expected}"


class TestTokenPersistence:
    def test_loads_tokens_from_file(self, tmp_path):
        token_file = tmp_path / "tokens.json"
        tokens = {
            "access_token": "loaded_token",
            "refresh_token": "loaded_refresh",
            "expires_at": time.time() + 3600,
        }
        token_file.write_text(json.dumps(tokens))

        client = EbayOAuth2Client(
            client_id="id",
            client_secret="secret",
            ru_name="ru",
            token_file=str(token_file),
        )

        assert client.get_valid_token() == "loaded_token"

    def test_handles_corrupted_token_file(self, tmp_path):
        token_file = tmp_path / "tokens.json"
        token_file.write_text("not valid json{{{")

        client = EbayOAuth2Client(
            client_id="id",
            client_secret="secret",
            ru_name="ru",
            token_file=str(token_file),
        )

        assert client._token_data is None
