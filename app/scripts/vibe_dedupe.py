"""Entrypoint for the duplicate-track report.

    python app/scripts/vibe_dedupe.py --exclude recap

Read-only: reports duplicates, never touches YouTube Music.
"""

from vibe.dedupe import main

if __name__ == "__main__":
    main()
