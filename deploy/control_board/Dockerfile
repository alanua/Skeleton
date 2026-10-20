# Base-image digest verified against docker-library/repo-info
# commit 790c4e06530b08748dab58701b2f18e280d837ff.
FROM python:3.12.13-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

COPY core/control_board /app/core/control_board
COPY fixtures/control_board /app/fixtures/control_board

RUN python -m pip install --no-cache-dir --disable-pip-version-check \
    "fastapi==0.116.1" \
    "jinja2==3.1.6" \
    "uvicorn==0.35.0" \
  && python -m py_compile \
    /app/core/control_board/__init__.py \
    /app/core/control_board/app.py \
    /app/core/control_board/contracts.py \
    /app/core/control_board/projections.py \
  && python -c "from core.control_board.app import app; assert app.docs_url is None" \
  && groupadd --system --gid 10001 controlboard \
  && useradd --system --uid 10001 --gid controlboard --home-dir /nonexistent --shell /usr/sbin/nologin controlboard \
  && chown -R controlboard:controlboard /app

EXPOSE 8080

USER controlboard:controlboard

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"

CMD ["uvicorn", "core.control_board.app:app", "--host", "0.0.0.0", "--port", "8080"]
