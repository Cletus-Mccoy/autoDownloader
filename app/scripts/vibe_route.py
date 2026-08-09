"""Entrypoint: sort liked tracks into playlists.

    python app/scripts/vibe_route.py            # dry run
    python app/scripts/vibe_route.py --execute  # actually files them
"""

from vibe.route import main

if __name__ == "__main__":
    main()
