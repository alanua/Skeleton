FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY core /app/core
COPY fixtures/control_board /app/fixtures/control_board

RUN python -m pip install --no-cache-dir .

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"

CMD ["uvicorn", "core.control_board.app:app", "--host", "0.0.0.0", "--port", "8080"]
