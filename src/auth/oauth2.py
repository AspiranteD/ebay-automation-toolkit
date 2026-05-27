"""
eBay OAuth 2.0 authentication with file-based token persistence.

Implements the full authorization code grant flow:
  1. Generate authorization URL with required scopes
  2. Exchange authorization code for access + refresh tokens
  3. Auto-refresh access token when expired (5-min buffer)

Token lifecycle:
  - Access token: ~2 hours (auto-refreshed)
  - Refresh token: ~18 months (one-time interactive login)

Scopes requested:
  - api_scope (general)
  - sell.inventory (manage listings)
  - sell.account (account settings)
  - sell.fulfillment (orders and shipping)
"""
import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import requests

logger = logging.getLogger(__name__)

EBAY_AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
EBAY_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
]

TOKEN_EXPIRY_BUFFER = 300


class EbayAuth:
    """
    Manages eBay OAuth 2.0 tokens with file-based persistence.

    Tokens are stored as JSON in a configurable file path. The access token
    is automatically refreshed when it expires or is within 5 minutes of
    expiring.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        ru_name: str,
        token_file: str = ".ebay_tokens.json",
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._ru_name = ru_name
        self._token_file = token_file

    def _get_basic_auth(self) -> str:
        creds = f"{self._client_id}:{self._client_secret}"
        return base64.b64encode(creds.encode()).decode()

    def _load_tokens(self) -> dict:
        if os.path.exists(self._token_file):
            with open(self._token_file, "r") as f:
                return json.load(f)
        return {}

    def _save_tokens(self, tokens: dict):
        with open(self._token_file, "w") as f:
            json.dump(tokens, f, indent=2)
        logger.info("Tokens saved to %s", self._token_file)

    def get_authorization_url(self) -> str:
        """Build the eBay consent URL for the authorization code grant flow."""
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._ru_name,
            "response_type": "code",
            "scope": " ".join(SCOPES),
        }
        return f"{EBAY_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, auth_code: str) -> dict:
        """Exchange an authorization code for access + refresh tokens."""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {self._get_basic_auth()}",
        }
        data = {
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": self._ru_name,
        }

        resp = requests.post(EBAY_TOKEN_URL, headers=headers, data=data, timeout=30)
        resp.raise_for_status()
        token_data = resp.json()

        tokens = {
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_at": time.time() + token_data.get("expires_in", 7200),
            "refresh_token_expires_at": time.time() + token_data.get(
                "refresh_token_expires_in", 47304000
            ),
            "obtained_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_tokens(tokens)
        logger.info("Tokens obtained via authorization code")
        return tokens

    def refresh(self, refresh_token: Optional[str] = None) -> dict:
        """Refresh the access token using the stored or provided refresh token."""
        tokens = self._load_tokens()
        rt = refresh_token or tokens.get("refresh_token")

        if not rt:
            raise ValueError("No refresh_token available. Run interactive login first.")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {self._get_basic_auth()}",
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "scope": " ".join(SCOPES),
        }

        resp = requests.post(EBAY_TOKEN_URL, headers=headers, data=data, timeout=30)
        resp.raise_for_status()
        token_data = resp.json()

        tokens["access_token"] = token_data["access_token"]
        tokens["expires_at"] = time.time() + token_data.get("expires_in", 7200)
        tokens["refreshed_at"] = datetime.now(timezone.utc).isoformat()
        if "refresh_token" in token_data:
            tokens["refresh_token"] = token_data["refresh_token"]

        self._save_tokens(tokens)
        logger.info("Access token refreshed")
        return tokens

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing if expired or near-expiry."""
        tokens = self._load_tokens()

        if not tokens.get("access_token"):
            if tokens.get("refresh_token"):
                logger.info("No access token, refreshing...")
                tokens = self.refresh()
            else:
                raise ValueError("No tokens available. Run interactive login first.")

        if time.time() >= tokens.get("expires_at", 0) - TOKEN_EXPIRY_BUFFER:
            logger.info("Access token expired or near-expiry, refreshing...")
            tokens = self.refresh()

        return tokens["access_token"]

    def get_status(self) -> dict:
        """Return token status: validity, remaining time, timestamps."""
        tokens = self._load_tokens()
        now = time.time()
        at_remaining = max(0, tokens.get("expires_at", 0) - now)
        rt_remaining = max(0, tokens.get("refresh_token_expires_at", 0) - now)
        return {
            "access_token_valid": at_remaining > 0,
            "access_token_remaining_minutes": round(at_remaining / 60),
            "refresh_token_valid": rt_remaining > 0,
            "refresh_token_remaining_days": round(rt_remaining / 86400),
            "obtained_at": tokens.get("obtained_at"),
            "refreshed_at": tokens.get("refreshed_at"),
        }

    @staticmethod
    def extract_code_from_url(callback_url: str) -> Optional[str]:
        """Extract the authorization code from an eBay callback URL."""
        if "code=" in callback_url:
            parsed = urlparse(callback_url)
            params = parse_qs(parsed.query)
            return params.get("code", [None])[0]
        if len(callback_url) > 20:
            return callback_url
        return None


def get_access_token(auth: EbayAuth) -> str:
    return auth.get_access_token()


def refresh_access_token(auth: EbayAuth) -> dict:
    return auth.refresh()
