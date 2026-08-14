FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

EXPOSE 8000
# 空の DB は起動時に表を作る。中身のある DB の差分は専用ページで控えを取ってから当てる
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
