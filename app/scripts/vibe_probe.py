"""Entrypoint for the playlist separability probe.

    python app/scripts/vibe_probe.py --sample 30

Running this file puts app/scripts on sys.path, so the vibe package can import
the service's existing ytmusic_auth / download helpers.
"""

from vibe.probe import main

if __name__ == "__main__":
    main()
