FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# rasterio's bundled GDAL needs libexpat, which the slim image leaves out
RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py geo.py mesh.py route.py ./
COPY static ./static

# run as an unprivileged user
RUN useradd --create-home appuser && chmod -R a+rX /app
USER appuser

# hosts like Render set $PORT; default 8000 for local `docker run`
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
