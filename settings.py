import os

SECRET_KEY = "proxy-secret-key-change-me"
DEBUG = os.environ.get("DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = ["*"]
ROOT_URLCONF = "urls"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
]

MIDDLEWARE = []

# Proxy listen port
PORT = 8779

# Target OpenAI-compatible API (uses OPENAI_BASE_URL from shell env)
TARGET_BASE_URL = os.environ["OPENAI_BASE_URL"]

# Fields to drop from request body before forwarding
DROP_FIELDS = [
    f.strip()
    for f in os.environ.get(
        "DROP_FIELDS",
        "stream_options,parallel_tool_calls,service_tier",
    ).split(",")
    if f.strip()
]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "proxy": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}
