# دستیار هوشمند کسب‌وکارهای مجازی — application image
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY templates ./templates
COPY static ./static

# Run as a non-root user (security baseline).
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Default: web interface. The worker service overrides this command.
CMD ["uvicorn", "app.interfaces.http.app:app", "--host", "0.0.0.0", "--port", "8000"]
