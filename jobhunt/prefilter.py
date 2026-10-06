"""Deterministic filter that runs BEFORE any LLM call.

This is the whole cost story: ~2000 raw jobs -> ~40 candidates for ~0 rupees,
so Claude only ever reads jobs that already passed title + location + freshness.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .fetch import Job

REMOTE_HINTS = ("remote", "anywhere", "work from home", "wfh", "distributed")


def _any_match(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.fromisoformat(v) if fmt is None else datetime.strptime(v, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def prefilter(jobs: list[Job], cfg: dict) -> list[Job]:
    inc = cfg.get("include_titles") or [r"."]
    exc = cfg.get("exclude_titles") or []
    location_cfg = cfg.get("locations") or []
    if isinstance(location_cfg, dict):
        locs = [l.lower() for l in (location_cfg.get("include") or [])]
        remote_confirm = location_cfg.get("remote_scope_confirm_phrases") or []
        remote_reject = location_cfg.get("remote_scope_reject_phrases") or []
    else:
        locs = [l.lower() for l in location_cfg]
        remote_confirm = cfg.get("remote_scope_confirm_phrases") or []
        remote_reject = cfg.get("remote_scope_reject_phrases") or []
    allow_remote = bool(cfg.get("allow_remote", True))
    max_age = cfg.get("max_age_days")
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age) if max_age else None

    kept, stats = [], {"title": 0, "location": 0, "age": 0}
    for j in jobs:
        if not _any_match(inc, j.title) or (exc and _any_match(exc, j.title)):
            stats["title"] += 1
            continue

        if locs:
            location_hay = f"{j.location} {j.title}".lower()
            full_hay = f"{location_hay} {j.description}"
            is_remote = any(h in location_hay for h in REMOTE_HINTS)
            if is_remote:
                remote_ok = allow_remote
                if remote_ok and remote_reject and _any_match(remote_reject, full_hay):
                    remote_ok = False
                broad_remote_scopes = {
                    *REMOTE_HINTS, "india", "anywhere", "global", "worldwide",
                    "apac", "asia",
                }
                target_location_in_board = any(
                    l in j.location.lower() for l in locs if l not in broad_remote_scopes
                )
                scoped_remote_location = re.search(
                    r"\bremote\b\s*[-,(]\s*\S", j.location, re.I)
                if remote_ok and not target_location_in_board and scoped_remote_location \
                        and not _any_match(remote_confirm, j.location):
                    remote_ok = False
                if remote_ok and not target_location_in_board and remote_confirm \
                        and not _any_match(remote_confirm, full_hay):
                    remote_ok = False
                if not remote_ok:
                    stats["location"] += 1
                    continue
            elif not any(l in location_hay for l in locs):
                stats["location"] += 1
                continue

        if cutoff:
            posted = _parse_date(j.posted_at)
            if posted and posted < cutoff:
                stats["age"] += 1
                continue

        kept.append(j)

    print(f"  prefilter: {len(jobs)} -> {len(kept)} "
          f"(dropped title={stats['title']} location={stats['location']} stale={stats['age']})")
    return kept
