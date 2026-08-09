FROM python:3.11-slim

WORKDIR /app

# Node comes from NodeSource, not Debian. Debian bookworm ships Node 20, which
# yt-dlp reports as "node-20.x (unsupported)" and then refuses to solve
# YouTube's signature/n challenges — every download fails with "Requested
# format is not available" and only storyboard images are offered. yt-dlp needs
# Node >= 22 (or deno) for its EJS challenge solver.
RUN apt-get update && apt-get install -y --no-install-recommends \
        cron ffmpeg curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && node --version \
    && rm -rf /var/lib/apt/lists/*

COPY ./app.py /app/app.py
# Whole directory, not index.html alone — the sorter's page lives here too and
# naming files one by one silently 404s anything new.
COPY ./templates/ /app/templates
COPY ./scripts/ /app/scripts
COPY ./tests/ /app/tests
COPY ./pytest.ini /app/pytest.ini

RUN mkdir -p /app/data/auth /app/data/logs /app/downloads

COPY ./requirements.txt /app/requirements.txt
COPY ./requirements-vibe.txt /app/requirements-vibe.txt

# The sorter runs on the schedule too, so its dependencies belong in the image.
# essentia-tensorflow is the bulk of it; it bundles TensorFlow and is what
# turns audio into embeddings.
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && pip install --no-cache-dir -r /app/requirements-vibe.txt

RUN apt-get autoremove -y && apt-get clean -y

COPY ./templates/crontab /etc/cron.d/ytmusic
RUN chmod 0644 /etc/cron.d/ytmusic

CMD ["sh", "-c", "cron -f & python /app/app.py"]