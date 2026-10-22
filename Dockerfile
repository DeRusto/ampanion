FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml ./
COPY ampanion ./ampanion
RUN pip install --no-cache-dir .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV AMPANION_CONFIG=/config
VOLUME /config
EXPOSE 8484

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/health')"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "ampanion"]
