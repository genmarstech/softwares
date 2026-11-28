# Velora API image: gunicorn serving Django. nginx (docker/nginx.conf) sits in front
# for static files and logos, and cloudflared on the host forwards to nginx.
# Python 3.14 matches the local venv; Django 6.1 and DRF 3.18 both list it.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STATIC_ROOT=/app/staticfiles \
    MEDIA_ROOT=/app/media

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Never run as root: a hole in a dependency then cannot write over the image.
# The uid matches the owner of ./media on the host (uploaded logos are a bind mount),
# or the API could not save a new logo. Build with --build-arg UID=$(id -u) if yours
# is not 1000.
ARG UID=1000
RUN useradd --uid ${UID} --no-create-home velora \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R velora:velora /app/staticfiles /app/media
USER velora

EXPOSE 8000

CMD ["gunicorn", "velora_main.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", \
     "--access-logfile", "-", "--error-logfile", "-"]
