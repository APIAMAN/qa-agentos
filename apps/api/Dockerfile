FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY apps/api /app
COPY agents /app/agents
COPY prompts /app/prompts
COPY models.yaml /app/models.yaml
COPY tools.yaml /app/tools.yaml
RUN pip install --no-cache-dir .

ENV PYTHONPATH=/app/src

CMD ["uvicorn", "qa_agentos.main:app", "--host", "0.0.0.0", "--port", "8000"]
