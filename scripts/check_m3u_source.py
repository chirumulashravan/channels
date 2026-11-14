#!/usr/bin/env python3
"""Validate that the India M3U source is healthy before updating the master playlist."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import Request, urlopen

DEFAULT_SOURCE_URL = "https://raw.githubusercontent.com/chirumulashravan/channels/main/country_playlists/IN-India.m3u"


def fetch_text(url: str, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": "channels-source-check/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8-sig", errors="replace")


def validate_m3u_text(content: str, min_entries: int = 1) -> tuple[bool, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "#EXTM3U":
        return False, "M3U file is missing the #EXTM3U header"

    seen_urls = 0
    pending_extinf = False
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#EXTINF:"):
            pending_extinf = True
            continue
        if stripped.startswith("#"):
            continue
        if pending_extinf and stripped.startswith(("http://", "https://", "rtmp://", "rtsp://", "udp://")):
            seen_urls += 1
            pending_extinf = False
            continue
        if pending_extinf:
            pending_extinf = False

    if seen_urls < min_entries:
        return False, f"M3U file contains only {seen_urls} valid stream URL(s); expected at least {min_entries}"

    return True, f"M3U file looks valid with {seen_urls} stream URL(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL, help="Raw M3U URL to validate.")
    parser.add_argument(
        "--source-path",
        default="country_playlists/IN-India.m3u",
        help="Repository-local M3U file to validate alongside the remote URL.",
    )
    parser.add_argument("--min-entries", type=int, default=1, help="Minimum number of stream URLs required.")
    args = parser.parse_args()

    repo_path = Path(__file__).resolve().parents[1] / args.source_path
    if not repo_path.exists():
        print(f"Local source file is missing: {repo_path}")
        return 1

    local_text = repo_path.read_text(encoding="utf-8", errors="replace")
    ok, local_message = validate_m3u_text(local_text, args.min_entries)
    if not ok:
        print(f"Local source validation failed: {local_message}")
        return 1

    try:
        remote_text = fetch_text(args.source_url)
    except Exception as exc:  # pragma: no cover - network failure reporting
        print(f"Remote source validation failed: {exc}")
        return 1

    ok, remote_message = validate_m3u_text(remote_text, args.min_entries)
    if not ok:
        print(f"Remote source validation failed: {remote_message}")
        return 1

    print(f"Local source OK: {local_message}")
    print(f"Remote source OK: {remote_message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
