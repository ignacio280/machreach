# Production image for the VPS stack in deploy/. Same interpreter line, same
# locked dependencies, and the same gunicorn command as the Render service,
# so nothing about the app changes when the host does.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall them.
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock

COPY . .

# Not root inside the container. The SQLite fallback is never used here
# (DATABASE_URL is always set), so nothing needs to be writable but /tmp.
RUN useradd --system --uid 1000 --create-home app && chown -R app:app /app
USER app

EXPOSE 5000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "8", \
     "--preload", "--timeout", "120", "--max-requests", "5000", "--max-requests-jitter", "500"]
