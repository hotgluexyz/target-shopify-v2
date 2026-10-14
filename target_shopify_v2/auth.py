"""Refresh expiring Shopify offline access tokens.

A config with a ``refresh_token`` holds an expiring access token. It is refreshed
with ``grant_type=refresh_token`` when it is missing or near expiry, and once
more when Shopify answers a request with 401. Configs without a refresh_token
are never refreshed.

``expires_in`` is Shopify's token lifetime in seconds until this target refreshes
the token, after which it holds the Unix time the token expires.

Sinks copy the config, so the live tokens are kept on the target's config.

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
from singer_sdk.exceptions import FatalAPIError

REFRESH_SECONDS_BEFORE_EXPIRY = 120
# Lifetimes from Shopify are about an hour; Unix times are above this
SMALLEST_UNIX_TIME = 10**9

_one_refresh_at_a_time = threading.Lock()


def uses_expiring_token(target):
    # A null refresh_token in the config file arrives as SecretString(None), which is truthy
    refresh_token = target._config.get("refresh_token")
    return bool(getattr(refresh_token, "contents", refresh_token))


def _needs_refresh(config):
    expires_in = config.get("expires_in")
    if not config.get("access_token") or expires_in is None:
        return True
    if expires_in < SMALLEST_UNIX_TIME:
        return True
    return expires_in - time.time() <= REFRESH_SECONDS_BEFORE_EXPIRY


class TransientRefreshError(Exception):
    """Shopify answered the refresh with 429 or 5xx; the same refresh token can be retried."""


# The old refresh token stays valid until the new one is used, so retrying is safe even if
# Shopify processed an attempt whose response never arrived
@backoff.on_exception(
    backoff.expo,
    (TransientRefreshError, requests.exceptions.ConnectionError, requests.exceptions.Timeout),
    max_tries=5,
)
def _request_new_tokens(config):
    shop = config["shop"].split(".")[0]
    response = requests.post(
        f"https://{shop}.myshopify.com/admin/oauth/access_token",
        data={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": config["refresh_token"],
        },
        timeout=300,
    )
    if response.status_code == 429 or response.status_code >= 500:
        raise TransientRefreshError(
            f"Failed to refresh access token (HTTP {response.status_code}): {response.text}"
        )
    if not response.ok:
        raise FatalAPIError(f"Failed to refresh access token: {response.text}")
    return response.json()


def _refresh_access_token(target):
    config = target._config
    requested_at = int(time.time())
    token = _request_new_tokens(config)
    new_tokens = {
        "access_token": token["access_token"],
        "refresh_token": token["refresh_token"],
        "expires_in": requested_at + token["expires_in"],
    }
    _save_tokens_to_config_file(target._config_file_path, new_tokens)
    config.update(new_tokens)
    target.logger.info("Refreshed the Shopify access token.")


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


def get_valid_access_token(target):
    with _one_refresh_at_a_time:
        if _needs_refresh(target._config):
            _refresh_access_token(target)
    return target._config["access_token"]


def refresh_access_token_after_401(target, rejected_token):
    """Refresh after Shopify rejects ``rejected_token``.

    When another sink already replaced it, the resend uses that newer token instead
    of refreshing (and rotating the token pair) again.
    """
    with _one_refresh_at_a_time:
        already_refreshed_by_another_sink = target._config["access_token"] != rejected_token
        if not already_refreshed_by_another_sink:
            _refresh_access_token(target)
