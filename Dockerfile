FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY leagueassistant ./leagueassistant
RUN pip install --no-cache-dir ".[server]" \
    && useradd --system --uid 1000 app

USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["leagueassistant", "serve", "--host", "0.0.0.0", "--port", "8080"]
