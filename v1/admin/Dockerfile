FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY admin/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir hatchling

COPY pyproject.toml /app/pyproject.toml
COPY src/ /app/src/
RUN pip install --no-cache-dir --no-build-isolation /app

COPY admin/thunder_admin/ /app/thunder_admin/
COPY admin/.streamlit/ /app/.streamlit/

ENV PYTHONPATH=/app

COPY admin/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8501

HEALTHCHECK CMD curl -sf http://localhost:8501/_stcore/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
