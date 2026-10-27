"""Configuración normal del proyecto, alimentada por el entorno local."""

import environ

from .common_settings import *

env = environ.Env(DEBUG=(bool, False))

# Esta entrada normal carga .env. core.test_settings no importa este módulo.
ENV_FILE = BASE_DIR / ".env"
environ.Env.read_env(ENV_FILE)

SECRET_KEY = env("SECRET_KEY")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# PythonAnywhere entrega HTTPS mediante su proxy inverso.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=3600)
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

INTERNAL_IPS = env.list("INTERNAL_IPS", default=[])

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    if DATABASES["default"]["NAME"] != ":memory:":
        DATABASES["default"]["NAME"] = BASE_DIR / DATABASES["default"]["NAME"]
    DATABASES["default"].setdefault("OPTIONS", {}).setdefault("timeout", 10)

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "media"

# Cada proyecto debe configurar y probar su proveedor antes de sustituir el
# backend de consola o exigir verificación de correo.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# django-axes y preventconcurrentlogins permanecen instalados, pero desactivados.
# Cada proyecto derivado debe decidir y probar su activación explícitamente.
# AXES_FAILURE_LIMIT = 3
# AXES_COOLOFF_TIME = ...
# AXES_RESET_ON_SUCCESS = True
# AXES_ENABLE_ACCESS_FAILURE_LOG = True
# AXES_LOCK_OUT_AT_FAILURE = True

if not DEBUG:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {"console": {"class": "logging.StreamHandler"}},
        "root": {"handlers": ["console"], "level": "WARNING"},
    }
