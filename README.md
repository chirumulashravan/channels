# Channels playlist

`channels.m3u` is generated from the source playlist page at
<https://iptv.b2og.com/list/o_all.list>.

The GitHub Actions workflow checks the source every 30 minutes and can also be
started manually. It regenerates and commits `channels.m3u` only when the
playlist has changed. The source currently serves an HTML table, so
`scripts/update_playlist.py` converts its channel names and stream URLs to
Extended M3U format.

The scheduled workflow requires this repository to be hosted on GitHub and
Actions to have permission to write repository contents.
