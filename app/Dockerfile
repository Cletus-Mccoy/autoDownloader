FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    cron ffmpeg curl nodejs\
    && rm -rf /var/lib/apt/lists/*

COPY ./app.py /app/app.py
COPY ./templates/index.html /app/templates/index.html
COPY ./scripts/ /app/scripts
COPY ./tests/ /app/tests
COPY ./pytest.ini /app/pytest.ini

RUN mkdir -p /app/data/auth /app/data/downloads /app/data/logs

COPY ./requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r /app/requirements.txt

RUN apt-get autoremove -y && apt-get clean -y

COPY ./templates/crontab /etc/cron.d/ytmusic
RUN chmod 0644 /etc/cron.d/ytmusic

CMD ["sh", "-c", "cron -f & python /app/app.py"]