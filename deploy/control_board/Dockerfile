FROM python:3.12.13-slim-bookworm@sha256:6f4378f2f3d2f0897d4f02f0eaf6f71f8c0d1c2f68b5c3dd4c9f9654f0f0b1bb

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY core /app/core
COPY fixtures/control_board /app/fixtures/control_board

RUN python -m pip install --no-cache-dir \
    . \
    "fastapi==0.116.1" \
    "jinja2==3.1.6" \
    "uvicorn[standard]==0.35.0" \
  && groupadd --system --gid 10001 controlboard \
  && useradd --system --uid 10001 --gid controlboard --home-dir /nonexistent --shell /usr/sbin/nologin controlboard \
  && chown -R controlboard:controlboard /app

EXPOSE 8080

USER controlboard:controlboard

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"

CMD ["uvicorn", "core.control_board.app:app", "--host", "0.0.0.0", "--port", "8080"]
