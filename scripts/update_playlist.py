#!/usr/bin/env python3
"""Generate a master playlist and country-specific playlists."""

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCES_CONFIG_PATH = REPOSITORY_ROOT / "playlist_sources.json"
SOURCE_URL = "https://iptv.b2og.com/list/o_all.list"
CHANNELS_URL = "https://raw.githubusercontent.com/iptv-org/database/master/data/channels.csv"
COUNTRIES_URL = "https://iptv-org.github.io/api/countries.json"
LANGUAGES_URL = "https://iptv-org.github.io/api/languages.json"
CATEGORIES_URL = "https://iptv-org.github.io/api/categories.json"
OUTPUT_PATH = REPOSITORY_ROOT / "channels.m3u"
COUNTRY_OUTPUT_DIR = OUTPUT_PATH.parent / "country_playlists"
MANIFEST_PATH = COUNTRY_OUTPUT_DIR / ".generated-playlists.json"
SOURCE_PLAYLIST_MANIFEST_PATH = REPOSITORY_ROOT / ".generated-source-playlists.json"
MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_METADATA_BYTES = 20 * 1024 * 1024
RESOLUTION = re.compile(r"\s*\(\s*(?:\d{3,4}\s*[pi]|[48]k)(?:\s+[^)]*)?\s*\)", re.IGNORECASE)
STATUS = re.compile(r"\s*\[(?:not 24/7|geo-blocked)\]\s*$", re.IGNORECASE)
FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
GENERATED_FOLDER_PLAYLIST = "combined.m3u"


@dataclass(frozen=True)
class PlaylistEntry:
    name: str
    url: str
    extinf: str | None = None


def fetch(url: str, max_bytes: int) -> bytes:
    request = Request(url, headers={"User-Agent": "channels-playlist-updater/1.0"})
    with urlopen(request, timeout=30) as response:
        content = response.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError(f"{url} exceeds {max_bytes} bytes")
    return content


def repository_path(value: str, description: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{description} must be a repository-relative path: {value}")
    resolved = (REPOSITORY_ROOT / path).resolve()
    if not resolved.is_relative_to(REPOSITORY_ROOT):
        raise ValueError(f"{description} must stay inside the repository: {value}")
    return resolved


def load_m3u_sources() -> list[tuple[str, Path, Path | None]]:
    config = json.loads(SOURCES_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or set(config) - {"m3u_sources", "example"}:
        raise ValueError(
            f"{SOURCES_CONFIG_PATH} may contain only 'm3u_sources' and 'example'"
        )
    sources = config["m3u_sources"]
    if not isinstance(sources, list):
        raise ValueError("'m3u_sources' must be a list")
    example = config.get("example")
    if example is not None and (
        not isinstance(example, dict)
        or set(example) != {"source", "output_folder"}
        or not all(isinstance(value, str) for value in example.values())
    ):
        raise ValueError("'example' must contain string source and output_folder fields")

    parsed_sources: list[tuple[str, Path, Path | None]] = []
    for index, item in enumerate(sources):
        if not isinstance(item, dict) or set(item) - {"source", "output_folder"}:
            raise ValueError(f"m3u_sources[{index}] must have source and optional output_folder")
        source = item.get("source")
        output_folder = item.get("output_folder", ".")
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"m3u_sources[{index}].source must be a non-empty URL or path")
        if not isinstance(output_folder, str):
            raise ValueError(f"m3u_sources[{index}].output_folder must be a path")

        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            source_url = source
            source_path = None
        else:
            source_url = ""
            source_path = repository_path(source, f"m3u_sources[{index}].source")
            if not source_path.is_file():
                raise ValueError(f"M3U source file does not exist: {source}")
            if source_path == OUTPUT_PATH:
                raise ValueError(f"The generated master playlist cannot be an M3U source: {source}")
        folder = repository_path(output_folder, f"m3u_sources[{index}].output_folder")
        output_path = folder / GENERATED_FOLDER_PLAYLIST
        if source_path is not None and source_path == output_path.resolve():
            raise ValueError(f"Output playlist cannot overwrite its M3U source: {source}")
        parsed_sources.append((source_url or str(source_path), folder, source_path))
    return parsed_sources


def parse_m3u(data: bytes, source_name: str) -> list[PlaylistEntry]:
    text = data.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "#EXTM3U":
        raise ValueError(f"M3U source does not start with #EXTM3U: {source_name}")

    entries: list[PlaylistEntry] = []
    pending_extinf: str | None = None
    for line_number, raw_line in enumerate(lines[1:], start=2):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            if pending_extinf is not None:
                raise ValueError(f"M3U entry is missing a stream URL: {source_name}:{line_number}")
            if "," not in line:
                raise ValueError(f"Malformed #EXTINF line: {source_name}:{line_number}")
            pending_extinf = line
        elif line.startswith("#"):
            continue
        elif pending_extinf is not None:
            title = pending_extinf.partition(",")[2].strip()
            if not title:
                raise ValueError(f"M3U channel has no name: {source_name}:{line_number}")
            entries.append(PlaylistEntry(title, line, pending_extinf))
            pending_extinf = None

    if pending_extinf is not None:
        raise ValueError(f"M3U entry is missing a stream URL: {source_name}")
    if not entries:
        raise ValueError(f"M3U source contained no channel entries: {source_name}")
    return entries


def deduplicate_entries(entries: list[PlaylistEntry]) -> list[PlaylistEntry]:
    seen_urls: set[str] = set()
    unique_entries = []
    for entry in entries:
        if entry.url not in seen_urls:
            seen_urls.add(entry.url)
            unique_entries.append(entry)
    return unique_entries


def normalize_name(name: str) -> str:
    name = unicodedata.normalize("NFKD", name.casefold())
    name = "".join(char for char in name if not unicodedata.combining(char))
    name = RESOLUTION.sub(" ", name)
    name = STATUS.sub("", name)
    return "".join(char for char in name if char.isalnum())


def attribute(value: str) -> str:
    return " ".join(value.replace('"', "'").split())


def load_channel_metadata(
    channels_csv: bytes,
    countries_json: bytes,
    languages_json: bytes,
    categories_json: bytes,
) -> dict[str, dict[str, object]]:
    countries = {country["code"]: country for country in json.loads(countries_json)}
    languages = {language["code"]: language["name"] for language in json.loads(languages_json)}
    categories = {category["id"]: category["name"] for category in json.loads(categories_json)}

    index: dict[str, dict[str, dict[str, object]]] = {}
    reader = csv.DictReader(io.StringIO(channels_csv.decode("utf-8-sig")))
    required = {"id", "name", "alt_names", "country", "categories"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("Channel metadata CSV is missing required columns")

    for channel in reader:
        country_code = channel["country"].strip()
        country = countries.get(country_code)
        language_codes = country.get("languages", []) if country else []
        metadata: dict[str, object] = {
            "country_code": country_code if country else "",
            "country_name": country["name"] if country else "",
            "language_codes": language_codes,
            "language_names": [languages[code] for code in language_codes if code in languages],
            "categories": [
                categories[category]
                for category in channel["categories"].split(";")
                if category in categories
            ],
        }

        for name in [channel["name"], *channel["alt_names"].split(";")]:
            key = normalize_name(name)
            if key:
                index.setdefault(key, {})[channel["id"]] = metadata

    # Keep only unambiguous name and alias matches.
    return {
        name: next(iter(matches.values()))
        for name, matches in index.items()
        if len(matches) == 1
    }


def channel_tags(name: str, metadata_index: dict[str, dict[str, object]]) -> str:
    metadata = metadata_index.get(normalize_name(name))
    if not metadata:
        return ""

    groups = []
    if metadata["country_name"]:
        groups.append(f'Country: {metadata["country_name"]}')
    groups.extend(f"Type: {category}" for category in metadata["categories"])
    groups.extend(f"Language: {language}" for language in metadata["language_names"])

    attributes = []
    if metadata["country_code"]:
        attributes.append(f'tvg-country="{attribute(str(metadata["country_code"]))}"')
    if metadata["language_codes"]:
        codes = ",".join(str(code) for code in metadata["language_codes"])
        attributes.append(f'tvg-language="{attribute(codes)}"')
    if groups:
        attributes.append(f'group-title="{attribute(";".join(groups))}"')
    return " " + " ".join(attributes)


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


def parse_channels(source_html: str) -> list[PlaylistEntry]:
    parser = ChannelTableParser()
    parser.feed(source_html)

    entries: list[PlaylistEntry] = []
    for row in parser.rows:
        if len(row) < 4:
            continue
        name = row[1][0]
        links = row[3][1]
        url = links[0] if links else row[3][0]
        if name and url.startswith(("http://", "https://")):
            entries.append(PlaylistEntry(name, url))

    if not entries:
        raise ValueError("The source page contained no valid channel entries")

    return entries


def build_playlist(
    entries: list[PlaylistEntry],
    metadata_index: dict[str, dict[str, object]],
) -> str:
    lines = ["#EXTM3U"]
    for entry in entries:
        if entry.extinf:
            extinf = add_channel_tags(entry.extinf, entry.name, metadata_index)
        else:
            extinf = f"#EXTINF:-1{channel_tags(entry.name, metadata_index)},{entry.name}"
        lines.extend((extinf, entry.url))
    return "\n".join(lines) + "\n"


def add_channel_tags(
    extinf: str,
    name: str,
    metadata_index: dict[str, dict[str, object]],
) -> str:
    tags = channel_tags(name, metadata_index).strip()
    if not tags:
        return extinf

    header, separator, title = extinf.partition(",")
    if not separator:
        return extinf
    tag_values = re.findall(r'([\w-]+)="([^"]*)"', tags)
    for key, _ in tag_values:
        header = re.sub(
            rf'\s+{re.escape(key)}="[^"]*"',
            "",
            header,
            flags=re.IGNORECASE,
        )
    return f'{header} {tags},{title}'


def country_filename(country_code: str, country_name: str) -> str:
    country_label = FILENAME_CHARS.sub("-", country_name).strip("-._")
    if not country_label:
        country_label = "Unknown"
    return f"{country_code}-{country_label}.m3u"


def build_country_playlists(
    entries: list[PlaylistEntry],
    metadata_index: dict[str, dict[str, object]],
) -> dict[str, str]:
    countries: dict[str, tuple[str, list[PlaylistEntry]]] = {}
    unclassified: list[PlaylistEntry] = []

    for entry in entries:
        metadata = metadata_index.get(normalize_name(entry.name))
        country_code = str(metadata["country_code"]) if metadata else ""
        country_name = str(metadata["country_name"]) if metadata else ""
        if not country_code or not country_name:
            unclassified.append(entry)
            continue
        countries.setdefault(country_code, (country_name, []))[1].append(entry)

    playlists = {}
    for country_code, (country_name, country_entries) in countries.items():
        playlists[country_filename(country_code, country_name)] = build_playlist(
            country_entries,
            metadata_index,
        )

    if unclassified:
        playlists["Unclassified.m3u"] = build_playlist(unclassified, metadata_index)

    return playlists


def write_source_folder_playlists(
    folder_entries: dict[Path, list[PlaylistEntry]],
    metadata_index: dict[str, dict[str, object]],
) -> None:
    previous_files: set[str] = set()
    if SOURCE_PLAYLIST_MANIFEST_PATH.exists():
        previous = json.loads(SOURCE_PLAYLIST_MANIFEST_PATH.read_text(encoding="utf-8"))
        if not isinstance(previous, list) or not all(
            isinstance(name, str)
            and Path(name).name == GENERATED_FOLDER_PLAYLIST
            and not Path(name).is_absolute()
            and ".." not in Path(name).parts
            for name in previous
        ):
            raise ValueError(
                f"Invalid generated source-playlist manifest: {SOURCE_PLAYLIST_MANIFEST_PATH}"
            )
        previous_files = set(previous)

    current_files = {
        str((folder / GENERATED_FOLDER_PLAYLIST).resolve().relative_to(REPOSITORY_ROOT))
        for folder in folder_entries
    }
    if not all(
        (REPOSITORY_ROOT / name).resolve().is_relative_to(REPOSITORY_ROOT)
        for name in current_files
    ):
        raise ValueError("Generated source playlist path must stay inside the repository")

    for folder, entries in folder_entries.items():
        output = folder / GENERATED_FOLDER_PLAYLIST
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".m3u.tmp")
        temporary.write_text(
            build_playlist(entries, metadata_index),
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(output)

    for filename in previous_files - current_files:
        stale_output = repository_path(filename, "generated source-playlist manifest entry")
        stale_output.unlink(missing_ok=True)

    temporary_manifest = SOURCE_PLAYLIST_MANIFEST_PATH.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(sorted(current_files), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary_manifest.replace(SOURCE_PLAYLIST_MANIFEST_PATH)


def write_country_playlists(playlists: dict[str, str]) -> None:
    COUNTRY_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    previous_files: set[str] = set()
    if MANIFEST_PATH.exists():
        previous = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if not isinstance(previous, list) or not all(
            isinstance(name, str) and Path(name).name == name and name.endswith(".m3u")
            for name in previous
        ):
            raise ValueError(f"Invalid generated-playlist manifest: {MANIFEST_PATH}")
        previous_files = set(previous)

    current_files = set(playlists)
    if not all(Path(name).name == name and name.endswith(".m3u") for name in current_files):
        raise ValueError("Invalid generated country playlist filename")

    for filename, content in playlists.items():
        output = COUNTRY_OUTPUT_DIR / filename
        temporary = output.with_suffix(".m3u.tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(output)

    for filename in previous_files - current_files:
        (COUNTRY_OUTPUT_DIR / filename).unlink(missing_ok=True)

    temporary_manifest = MANIFEST_PATH.with_suffix(".json.tmp")
    temporary_manifest.write_text(
        json.dumps(sorted(current_files), indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary_manifest.replace(MANIFEST_PATH)


def main() -> None:
    source = fetch(SOURCE_URL, MAX_SOURCE_BYTES)
    channels = fetch(CHANNELS_URL, MAX_METADATA_BYTES)
    countries = fetch(COUNTRIES_URL, MAX_METADATA_BYTES)
    languages = fetch(LANGUAGES_URL, MAX_METADATA_BYTES)
    categories = fetch(CATEGORIES_URL, MAX_METADATA_BYTES)
    metadata_index = load_channel_metadata(channels, countries, languages, categories)
    source_html = source.decode("utf-8", errors="replace")
    html_entries = parse_channels(source_html)
    m3u_sources = load_m3u_sources()
    entries_by_folder: dict[Path, list[PlaylistEntry]] = {}
    all_entries = list(html_entries)
    for source_name, folder, source_path in m3u_sources:
        if source_path is not None:
            data = source_path.read_bytes()
            if len(data) > MAX_SOURCE_BYTES:
                raise ValueError(f"{source_name} exceeds {MAX_SOURCE_BYTES} bytes")
        else:
            data = fetch(source_name, MAX_SOURCE_BYTES)
        source_entries = parse_m3u(data, source_name)
        entries_by_folder.setdefault(folder, []).extend(source_entries)
        all_entries.extend(source_entries)

    entries = deduplicate_entries(all_entries)
    entries_by_folder = {
        folder: deduplicate_entries(folder_entries)
        for folder, folder_entries in entries_by_folder.items()
    }
    playlist = build_playlist(entries, metadata_index)
    country_playlists = build_country_playlists(entries, metadata_index)

    temporary_path = OUTPUT_PATH.with_suffix(".m3u.tmp")
    temporary_path.write_text(playlist, encoding="utf-8", newline="\n")
    temporary_path.replace(OUTPUT_PATH)
    write_country_playlists(country_playlists)
    write_source_folder_playlists(entries_by_folder, metadata_index)
    classified = sum(
        content.count("#EXTINF:")
        for filename, content in country_playlists.items()
        if filename != "Unclassified.m3u"
    )
    country_count = len(country_playlists) - ("Unclassified.m3u" in country_playlists)
    print(
        f"Generated {OUTPUT_PATH.name} with {len(entries)} channels, plus "
        f"{country_count} country playlists containing {classified} classified channels, "
        f"and {len(entries_by_folder)} source-folder playlists"
    )


if __name__ == "__main__":
    main()
