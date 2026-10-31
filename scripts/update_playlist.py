#!/usr/bin/env python3
"""Generate channels.m3u from the source playlist's rendered HTML table."""

from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

SOURCE_URL = "https://iptv.b2og.com/list/o_all.list"
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "channels.m3u"
MAX_SOURCE_BYTES = 10 * 1024 * 1024


class ChannelTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[tuple[str, list[str]]]] = []
        self._row: list[tuple[str, list[str]]] | None = None
        self._cell_text: list[str] | None = None
        self._cell_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell_text = []
            self._cell_links = []
        elif tag == "a" and self._cell_text is not None:
            href = dict(attrs).get("href")
            if href:
                self._cell_links.append(href)

    def handle_data(self, data: str) -> None:
        if self._cell_text is not None:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._cell_text is not None and self._row is not None:
            text = " ".join("".join(self._cell_text).split())
            self._row.append((text, self._cell_links))
            self._cell_text = None
            self._cell_links = []
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def build_playlist(source_html: str) -> str:
    parser = ChannelTableParser()
    parser.feed(source_html)

    entries: list[tuple[str, str]] = []
    for row in parser.rows:
        if len(row) < 4:
            continue
        name = row[1][0]
        links = row[3][1]
        url = links[0] if links else row[3][0]
        if name and url.startswith(("http://", "https://")):
            entries.append((name, url))

    if not entries:
        raise ValueError("The source page contained no valid channel entries")

    lines = ["#EXTM3U"]
    for name, url in entries:
        lines.extend((f"#EXTINF:-1,{name}", url))
    return "\n".join(lines) + "\n"


def main() -> None:
    request = Request(SOURCE_URL, headers={"User-Agent": "channels-playlist-updater/1.0"})
    with urlopen(request, timeout=30) as response:
        source = response.read(MAX_SOURCE_BYTES + 1)
    if len(source) > MAX_SOURCE_BYTES:
        raise ValueError(f"Source page exceeds {MAX_SOURCE_BYTES} bytes")

    playlist = build_playlist(source.decode("utf-8", errors="replace"))
    temporary_path = OUTPUT_PATH.with_suffix(".m3u.tmp")
    temporary_path.write_text(playlist, encoding="utf-8", newline="\n")
    temporary_path.replace(OUTPUT_PATH)
    print(f"Generated {OUTPUT_PATH.name} with {playlist.count('#EXTINF:')} channels")


if __name__ == "__main__":
    main()
