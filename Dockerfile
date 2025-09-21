# Use a small base image
FROM python:3.12-slim

# Prevents Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set workdir
WORKDIR /app

# System deps (optional minimal set; keep slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app
COPY app /app/app

# Cloud Run listens on $PORT; default to 8080 for local parity
ENV PORT=8080

# Use gunicorn for production serving
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app.main:app"]
