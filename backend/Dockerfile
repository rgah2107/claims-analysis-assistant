# One image runs as the API or as the Celery worker.
# The command in docker-compose.yml decides which process starts.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 1000 --create-home --shell /usr/sbin/nologin claims

COPY app ./app
COPY eval ./eval
COPY docker_entrypoint.py /docker_entrypoint.py

EXPOSE 8000
ENTRYPOINT ["python", "/docker_entrypoint.py"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
