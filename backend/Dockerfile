FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements*.txt /app/backend/
RUN pip install --no-cache-dir -r backend/requirements.lock.txt
COPY backend /app/backend
COPY scripts/bootstrap.py /app/scripts/bootstrap.py
COPY downloads /app/downloads
ENV DATA_DIR=/app/data PYTHONUNBUFFERED=1
CMD ["sh", "-c", "python scripts/bootstrap.py && uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000"]
