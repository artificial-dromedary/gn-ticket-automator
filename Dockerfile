FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        libnss3 \
        libgconf-2-4 \
        libxss1 \
        libasound2 \
        libatk1.0-0 \
        libgtk-3-0 \
        libgbm1 \
        libx11-xcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libxfixes3 \
        libdrm2 \
        libxkbcommon0 \
        libxshmfence1 \
        fonts-liberation \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

ENV PATH="/usr/bin:${PATH}"

CMD ["gunicorn", "main:app", "-b", "0.0.0.0:10000"]
