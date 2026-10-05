FROM python:3.11-slim

WORKDIR /srv
ENV PYTHONPATH=/srv/app \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080

COPY app/ /srv/app/
COPY tests/ /srv/tests/

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=2s --retries=10 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).status==200 else 1)"

CMD ["python", "-m", "cansim.server"]
