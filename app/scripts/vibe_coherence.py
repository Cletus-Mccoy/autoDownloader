"""Entrypoint: measure how tight each playlist is and what it overlaps.

    python app/scripts/vibe_coherence.py

Read-only: works from cached embeddings, touches nothing.
"""

from vibe.coherence import main

if __name__ == "__main__":
    main()
