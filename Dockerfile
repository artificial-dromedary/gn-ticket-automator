FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# chromium pulls in its own runtime dependencies; fonts and CA certs are the only
# extras Selenium needs on top. (Do not add libgconf-2-4 — it no longer exists in
# Debian bookworm and its absence fails the whole apt step.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        fonts-liberation \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Where gn_ticket.py looks for the browser and driver.
ENV CHROME_BINARY=/usr/bin/chromium
ENV CHROMEDRIVER=/usr/bin/chromedriver

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

CMD ["gunicorn", "main:app", "-b", "0.0.0.0:10000"]
