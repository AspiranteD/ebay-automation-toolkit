"""
eBay OAuth2 Authorization Code Grant flow.

Handles the full lifecycle: authorization URL generation, code exchange,
token refresh, and automatic renewal on expiry.
"""

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests

EBAY_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

DEFAULT_SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
]


@dataclass
class TokenData:
    access_token: str
    refresh_token: str
    expires_at: float
    token_type: str = "Bearer"

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 60  # 60s safety margin

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenData":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            token_type=data.get("token_type", "Bearer"),
        )


class EbayOAuth2Client:
    """Manages eBay OAuth2 authorization code grant flow with automatic token refresh."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        ru_name: Optional[str] = None,
        scopes: Optional[list[str]] = None,
        token_file: str = "ebay_tokens.json",
    ):
        self.client_id = client_id or os.getenv("EBAY_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("EBAY_CLIENT_SECRET", "")
        self.ru_name = ru_name or os.getenv("EBAY_RUNAME", "")
        self.scopes = scopes or DEFAULT_SCOPES
        self.token_file = Path(token_file)
        self._token_data: Optional[TokenData] = None

        self._load_tokens()

    def _build_basic_auth(self) -> str:
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return f"Basic {encoded}"

    def get_authorization_url(self, state: str = "") -> str:
        """Build the eBay OAuth2 authorization URL for the consent screen."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.ru_name,
            "response_type": "code",
            "scope": " ".join(self.scopes),
        }
        if state:
            params["state"] = state
        return f"{EBAY_AUTH_URL}?{urlencode(params)}"

    def exchange_code_for_tokens(self, auth_code: str) -> TokenData:
        """Exchange an authorization code for access and refresh tokens."""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": self._build_basic_auth(),
        }
        payload = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": self.ru_name,
        }

        response = requests.post(EBAY_TOKEN_URL, headers=headers, data=payload)
        response.raise_for_status()
        data = response.json()

        self._token_data = TokenData(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=time.time() + data["expires_in"],
            token_type=data.get("token_type", "Bearer"),
        )
        self._save_tokens()
        return self._token_data

    def refresh_access_token(self) -> TokenData:
        """Renew the access token using the stored refresh token."""
        if not self._token_data or not self._token_data.refresh_token:
            raise ValueError("No refresh token available. Run authorization flow first.")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": self._build_basic_auth(),
        }
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._token_data.refresh_token,
            "scope": " ".join(self.scopes),
        }

        response = requests.post(EBAY_TOKEN_URL, headers=headers, data=payload)
        response.raise_for_status()
        data = response.json()

        self._token_data = TokenData(
            access_token=data["access_token"],
            refresh_token=self._token_data.refresh_token,
            expires_at=time.time() + data["expires_in"],
            token_type=data.get("token_type", "Bearer"),
        )
        self._save_tokens()
        return self._token_data

    def get_valid_token(self) -> str:
        """Return a valid access token, auto-refreshing if expired."""
        if not self._token_data:
            raise ValueError("No tokens available. Run authorization flow first.")

        if self._token_data.is_expired:
            self.refresh_access_token()

        return self._token_data.access_token

    def get_auth_header(self) -> dict[str, str]:
        """Return a ready-to-use Authorization header dict."""
        token = self.get_valid_token()
        return {"Authorization": f"Bearer {token}"}

    def _save_tokens(self) -> None:
        if self._token_data:
            self.token_file.write_text(
                json.dumps(self._token_data.to_dict(), indent=2)
            )

    def _load_tokens(self) -> None:
        if self.token_file.exists():
            try:
                data = json.loads(self.token_file.read_text())
                self._token_data = TokenData.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                self._token_data = None
