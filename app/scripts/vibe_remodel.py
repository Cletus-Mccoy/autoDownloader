"""Entrypoint for the playlist remodeller.

    python app/scripts/vibe_remodel.py plan
    python app/scripts/vibe_remodel.py apply            # dry run
    python app/scripts/vibe_remodel.py apply --execute  # actually moves

Proposes moving individual misfiled tracks between existing playlists. Never
merges playlists and never creates them; an unreviewed file moves nothing.
"""

from vibe.remodel import main

if __name__ == "__main__":
    main()
