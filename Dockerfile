FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY LICENSE ./LICENSE
# Source exports may have private host permissions; the runtime user needs read access.
RUN chmod -R a+rX /srv/app && chmod a+r /srv/LICENSE
USER 1000:1000
EXPOSE 8080
# Web-process liveness only. /readyz separately checks Plex, libraries, and the keeper.
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"
# The app reads forwarded client addresses itself, and only from PK_TRUSTED_PROXIES.
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8080", \
     "--no-proxy-headers", "--no-access-log"]
