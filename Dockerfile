FROM python:3.12-slim-bookworm

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY doctor /app/doctor
COPY entrypoint.sh /entrypoint.sh
RUN chmod -R a+rX /app && chmod 755 /entrypoint.sh

WORKDIR /app
ENV PYTHONUNBUFFERED=1
EXPOSE 8688
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8688/api/state',timeout=4)" || exit 1
ENTRYPOINT ["/entrypoint.sh"]
