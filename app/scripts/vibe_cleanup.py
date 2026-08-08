"""Entrypoint for duplicate cleanup.

    python app/scripts/vibe_cleanup.py plan --refresh-library
    python app/scripts/vibe_cleanup.py apply            # dry run
    python app/scripts/vibe_cleanup.py apply --execute  # actually removes

Removal is irreversible from here; `apply` is a dry run unless --execute.
"""

from vibe.cleanup import main

if __name__ == "__main__":
    main()
