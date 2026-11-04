# channels

`channels.m3u` is generated from the source playlist page at
<https://iptv.b2og.com/list/o_all.list>.

The GitHub Actions workflow checks the source daily at 00:00 UTC and can also
be started manually. It regenerates and commits the playlists only when they
have changed.

The source serves an HTML table with channel names and stream URLs.
`scripts/update_playlist.py` converts it to an Extended M3U master playlist
and creates one directly loadable playlist per matched country in
`country_playlists/`. `Unclassified.m3u` contains channels without a unique
country match. Load `channels.m3u` in Kodi for all channels, or load a country
playlist directly to limit the list by country.

Add optional M3U sources to [`playlist_sources.json`](./playlist_sources.json).
Each source can be an HTTP(S) M3U URL or a path to an M3U file inside this
repository. Set `output_folder` to choose where that source's folder-level
`combined.m3u` is written; sources assigned to the same folder are merged
together. The updater leaves source files unchanged, removes duplicate stream
URLs (keeping the first occurrence), and also includes the configured M3Us in
the master and country playlists. The config has an inactive `example` entry
pointing to [`examples/sample.m3u`](./examples/sample.m3u); copy its `source`
and `output_folder` values into `m3u_sources` and replace them with a real
source to enable it. Example URLs use `example.com` and are placeholders.
An active entry looks like:

```json
{
  "m3u_sources": [
    {
      "source": "https://example.com/news.m3u",
      "output_folder": "playlists/news"
    },
    {
      "source": "playlists/sports.m3u",
      "output_folder": "playlists/sports"
    }
  ]
}
```

All paths must stay inside the repository. The folder output is always named
`combined.m3u`; only these generated outputs are removed when they are no
longer configured.

Country playlists are enriched using the public
[iptv-org channel database](https://github.com/iptv-org/database)
and its [country](https://iptv-org.github.io/api/countries.json),
[language](https://iptv-org.github.io/api/languages.json), and
[category](https://iptv-org.github.io/api/categories.json) metadata. In Kodi,
the master and country playlists group matched channels under `Country:`,
`Type:`, and `Language:` groups. IPTV Simple Client supports multiple group
names in `group-title`, separated by semicolons, so a channel can appear in
each relevant group. `tvg-country` and `tvg-language` tags are also included.
Languages come from the country's official-language list and are estimates,
not verified channel broadcast languages.
Channels without a unique metadata match are left uncategorized rather than
assigned guessed metadata.

The scheduled workflow requires this repository to be hosted on GitHub and
Actions to have permission to write repository contents.
