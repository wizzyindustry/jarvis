"""Instagram (professional account, Instagram Login) and a Facebook Page, read-only.

Tokens come from the Meta App Dashboard ("Generate token", valid 60 days) and are
refreshed automatically once they're over a day old. Own-account apps use
Standard Access, which doesn't need App Review.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from . import secrets
from .google import NotConnected

IG = "https://graph.instagram.com"
FB = "https://graph.facebook.com"
VERSION = "v27.0"
DAY = 86400


def save_instagram_token(token: str) -> str:
    r = httpx.get(f"{IG}/{VERSION}/me", params={"fields": "user_id,username", "access_token": token}, timeout=20)
    r.raise_for_status()
    me = r.json()
    secrets.put("instagram", {"token": token, "saved": time.time(), "user_id": me.get("user_id"),
                              "username": me.get("username")})
    return me.get("username", "")


def save_facebook_page(page_id: str, token: str) -> str:
    r = httpx.get(f"{FB}/{VERSION}/{page_id}", params={"fields": "name", "access_token": token}, timeout=20)
    r.raise_for_status()
    secrets.put("facebook_page", {"token": token, "page_id": page_id, "saved": time.time()})
    return r.json().get("name", "")


def _instagram_token() -> dict[str, Any]:
    s = secrets.get("instagram")
    if not s:
        raise NotConnected("Instagram isn't connected.")
    age = time.time() - s.get("saved", 0)
    if age > 60 * DAY:
        raise NotConnected("The Instagram token expired. Generate a new one in the Meta dashboard.")
    if age > DAY:  # refresh keeps it alive for another 60 days
        try:
            r = httpx.get(f"{IG}/refresh_access_token",
                          params={"grant_type": "ig_refresh_token", "access_token": s["token"]}, timeout=20)
            r.raise_for_status()
            s["token"] = r.json()["access_token"]
            s["saved"] = time.time()
            secrets.put("instagram", s)
        except (httpx.HTTPError, KeyError):
            pass  # keep using the current token until it expires
    return s


def instagram_data() -> dict[str, Any]:
    s = _instagram_token()
    tok = s["token"]
    r = httpx.get(f"{IG}/{VERSION}/me", params={"fields": "user_id,username,followers_count,media_count",
                                                "access_token": tok}, timeout=20)
    r.raise_for_status()
    me = r.json()
    data = {"username": me.get("username"), "followers": me.get("followers_count"), "posts": me.get("media_count")}
    uid = me.get("user_id") or s.get("user_id")
    try:
        r = httpx.get(f"{IG}/{VERSION}/{uid}/insights", params={
            "metric": "reach,accounts_engaged,total_interactions", "period": "day", "metric_type": "total_value",
            "access_token": tok}, timeout=20)
        r.raise_for_status()
        for m in r.json().get("data", []):
            data[m["name"]] = m.get("total_value", {}).get("value")
    except httpx.HTTPError:
        data["insights_error"] = True  # insights need instagram_business_manage_insights
    return data


def facebook_data() -> dict[str, Any]:
    s = secrets.get("facebook_page")
    if not s:
        raise NotConnected("Facebook isn't connected.")
    r = httpx.get(f"{FB}/{VERSION}/{s['page_id']}", params={"fields": "name,followers_count,fan_count",
                                                          "access_token": s["token"]}, timeout=20)
    r.raise_for_status()
    d = r.json()
    return {"page": d.get("name"), "followers": d.get("followers_count"), "likes": d.get("fan_count")}
