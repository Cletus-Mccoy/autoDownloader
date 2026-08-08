"""Entrypoint for the model-configuration sweep.

    python app/scripts/vibe_sweep.py --backend effnet

Read-only: re-scores cached embeddings, downloads nothing.
"""

from vibe.sweep import main

if __name__ == "__main__":
    main()
