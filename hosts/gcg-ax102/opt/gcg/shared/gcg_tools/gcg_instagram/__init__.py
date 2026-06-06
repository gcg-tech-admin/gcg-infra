"""
gcg_instagram — Meta Graph API wrapper for Instagram MCP tools.

Read-only phase 1:
    instagram.saved_posts.list  — list saved posts with basic info
    instagram.saved_posts.get   — get full details of a saved post
    instagram.profile.get       — get basic profile info

API: Meta Graph API v20+ via Instagram Basic Display / Graph API
Auth: OAuth 2.0 long-lived token (stored in 1Password)

Future phases:
    instagram.feed.list         — browse feed
    instagram.feed.reels        — browse reels
    instagram.posts.search      — search posts by hashtag/account
    instagram.comments.list     — read comments
    instagram.insights.*        — post/story metrics (needs Business account)

Environment:
    INSTAGRAM_ACCESS_TOKEN — long-lived OAuth token
"""
import os
import logging
import requests

API_BASE = "https://graph.instagram.com/v21.0"
ME_ENDPOINT = f"{API_BASE}/me"
SAVED_ENDPOINT = f"{API_BASE}/{{user_id}}/saved"

log = logging.getLogger(__name__)


def _get_token() -> str:
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "INSTAGRAM_ACCESS_TOKEN not set. "
            "Get a long-lived token from Meta Graph API playground: "
            "https://developers.facebook.com/tools/explorer/"
        )
    return token


def _api_get(endpoint: str, params: dict = None, timeout: int = 15) -> dict:
    token = _get_token()
    if params is None:
        params = {}
    params.setdefault("access_token", token)
    params.setdefault("fields", "id,username,account_type,media_count")

    r = requests.get(endpoint, params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(
            f"Instagram API error: {data['error'].get('message', 'unknown')} "
            f"(code: {data['error'].get('code', '?')})"
        )
    return data


# ── Tool implementations ──────────────────────────────────────────


def get_profile() -> dict:
    """Return basic Instagram profile info."""
    return _api_get(ME_ENDPOINT)


def list_saved_posts(limit: int = 25) -> list[dict]:
    """List saved posts with id, caption, media_type, media_url, permalink, timestamp."""
    profile = get_profile()
    user_id = profile.get("id")
    if not user_id:
        raise RuntimeError(f"Could not resolve Instagram user ID from profile")

    data = _api_get(
        SAVED_ENDPOINT.format(user_id=user_id),
        params={
            "fields": "id,caption,media_type,media_url,permalink,timestamp,username",
            "limit": min(limit, 100),
        },
    )
    posts = data.get("data", [])
    log.info("Fetched %d saved posts for user_id=%s", len(posts), user_id)
    return posts


def get_saved_post(post_id: str) -> dict:
    """Get full details for a single saved post."""
    data = _api_get(
        f"{API_BASE}/{post_id}",
        params={
            "fields": "id,caption,media_type,media_url,permalink,timestamp,username,"
                      "children,comments_count,like_count,thumbnail_url,shortcode",
        },
    )
    return data
