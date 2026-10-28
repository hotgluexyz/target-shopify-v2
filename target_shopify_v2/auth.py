"""Shopify OAuth authenticator for expiring offline access tokens.

A config with a ``refresh_token`` holds an expiring access token. It is refreshed
when it is missing or near expiry, and once more when Shopify answers a request
with 401. Configs without a refresh_token are never refreshed.

Refreshing follows the SDK: the Hotglue access token API first, then Shopify's
``grant_type=refresh_token`` locally.

``expires_in`` is Shopify's token lifetime in seconds until a refresh, after which
it holds the Unix time the token expires.

https://shopify.dev/docs/apps/build/authentication-authorization/migrate-to-expiring-offline-access-tokens
"""

import json
import os
import shutil
import tempfile
import threading
import time

import backoff
import requests
from hotglue_etl_exceptions import InvalidCredentialsError
from hotglue_singer_sdk.helpers._util import utc_now
from hotglue_singer_sdk.target_sdk.auth import OAuthAuthenticator

ACCESS_TOKEN_HEADER = "X-Shopify-Access-Token"
REFRESH_SECONDS_BEFORE_EXPIRY = 120
# Lifetimes from Shopify are about an hour; Unix times are above this
SMALLEST_UNIX_TIME = 10**9

_one_refresh_at_a_time = threading.Lock()


def uses_expiring_token(config):
    # A null refresh_token in the config file arrives as SecretString(None), which is truthy
    refresh_token = config.get("refresh_token")
    return bool(getattr(refresh_token, "contents", refresh_token))


def shopify_token_url(config):
    shop = config["shop"].split(".")[0]
    return f"https://{shop}.myshopify.com/admin/oauth/access_token"


class TransientRefreshError(Exception):
    """Shopify answered the refresh with 429 or 5xx; the same refresh token can be retried."""


class ShopifyOAuthAuthenticator(OAuthAuthenticator):
    """Sends Shopify's access token header and refreshes expiring tokens."""

    @property
    def auth_headers(self) -> dict:
        with _one_refresh_at_a_time:
            if not self.is_token_valid():
                self.update_access_token()
        return {ACCESS_TOKEN_HEADER: str(self._config["access_token"])}

    def is_token_valid(self) -> bool:
        expires_in = self._config.get("expires_in")
        if not self._config.get("access_token") or expires_in is None:
            return False
        expires_at = int(expires_in)
        if expires_at < SMALLEST_UNIX_TIME:
            if self.last_refreshed is None:  # Shopify's lifetime, issued at an unknown time
                return False
            expires_at += int(self.last_refreshed.timestamp())
        return expires_at - time.time() > REFRESH_SECONDS_BEFORE_EXPIRY

    def refresh_after_401(self, rejected_token: str) -> None:
        """Refresh after Shopify rejects ``rejected_token``.

        When another sink already replaced it, the resend uses that newer token instead
        of refreshing (and rotating the token pair) again.
        """
        with _one_refresh_at_a_time:
            already_refreshed_by_another_sink = str(self._config["access_token"]) != rejected_token
            if not already_refreshed_by_another_sink:
                self.update_access_token()

    # The old refresh token stays valid until the new one is used, so retrying is safe even if
    # Shopify processed an attempt whose response never arrived
    @backoff.on_exception(
        backoff.expo,
        (TransientRefreshError, requests.exceptions.ConnectionError, requests.exceptions.Timeout),
        max_tries=5,
    )
    def _request_new_tokens(self) -> dict:
        response = requests.post(self._auth_endpoint, data=self.oauth_request_body, timeout=300)
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientRefreshError(
                f"Failed to refresh access token (HTTP {response.status_code}): {response.text}"
            )
        if not response.ok:
            self.state.update({"auth_error_response": response.text})
            raise InvalidCredentialsError(f"Failed OAuth login, response was '{response.text}'")
        return response.json()

    def _update_access_token_locally(self) -> None:
        request_time = utc_now()
        token = self._request_new_tokens()
        new_tokens = {
            "access_token": token["access_token"],
            "refresh_token": token["refresh_token"],
            "expires_in": int(request_time.timestamp()) + int(token["expires_in"]),
        }
        if self._config_file_path is not None:
            _save_tokens_to_config_file(self._config_file_path, new_tokens)
        self._config.update(new_tokens)
        self.access_token = new_tokens["access_token"]
        self.expires_in = new_tokens["expires_in"]
        self.last_refreshed = request_time
        self.logger.info("Refreshed the Shopify access token.")


def _save_tokens_to_config_file(config_path, new_tokens):
    """Write the config to a temp file and swap it in, so a failed write can't truncate it."""
    with open(config_path) as config_file:
        original_config_text = config_file.read()
    saved_config = json.loads(original_config_text)
    saved_config.update(new_tokens)
    config_dir = os.path.dirname(os.path.abspath(config_path))
    temp_fd, temp_path = tempfile.mkstemp(dir=config_dir, suffix=".tmp")
    try:
        with os.fdopen(temp_fd, "w") as temp_file:
            json.dump(saved_config, temp_file, indent=4)
        try:
            os.replace(temp_path, config_path)
        except OSError:
            # A config mounted on its own (single-file Docker bind mount) can't be renamed over,
            # so it is rewritten in place, and put back as it was if that write fails
            try:
                shutil.copyfile(temp_path, config_path)
            except OSError:
                with open(config_path, "w") as config_file:
                    config_file.write(original_config_text)
                raise
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
