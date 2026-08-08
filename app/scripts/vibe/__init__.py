"""Vibe sorter package.

Playlist titles routinely contain emoji, and the default Windows console
codepage (cp1252) can't encode them — printing one raises UnicodeEncodeError
mid-run. Force UTF-8 on the standard streams at import so any entrypoint in
this package is safe.
"""

import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # not a reconfigurable text stream
        pass
