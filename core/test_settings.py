"""Configuración aislada: no importa settings.py ni lee .env."""

import atexit
import tempfile
from pathlib import Path

from .common_settings import *

SECRET_KEY = "clave-exclusiva-y-no-secreta-para-pruebas"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1", "[::1]"]
CSRF_TRUSTED_ORIGINS = []
INTERNAL_IPS = []

SECURE_PROXY_SSL_HEADER = None
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
    }
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "calidad-base-tests",
    }
}

_temporary_files = tempfile.TemporaryDirectory(prefix="calidad-base-tests-")
atexit.register(_temporary_files.cleanup)
_temporary_root = Path(_temporary_files.name)
MEDIA_ROOT = _temporary_root / "media"
STATIC_ROOT = _temporary_root / "staticfiles"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
}
