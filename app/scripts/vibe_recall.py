"""Entrypoint: take back tracks placed by playlists that no longer qualify.

    python app/scripts/vibe_recall.py            # dry run
    python app/scripts/vibe_recall.py --execute  # actually removes
"""

from vibe.recall import main

if __name__ == "__main__":
    main()
