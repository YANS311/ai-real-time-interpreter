"""
Django settings for AI Real-Time Interpreter project.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv(
    "DJANGO_SECRET_KEY",
    "django-insecure-dev-key-change-in-production",
)

DEBUG = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

INSTALLED_APPS = [
    "daphne",
    "channels",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "interpreter",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

# --- Redis（Channels + 会话共享）---
REDIS_URL = os.getenv("REDIS_URL", "").strip()
REDIS_SESSIONS_ENABLED = os.getenv("REDIS_SESSIONS_ENABLED", "True").lower() in (
    "true",
    "1",
    "yes",
)
REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "20"))
REDIS_SOCKET_TIMEOUT = float(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
REDIS_CONNECT_TIMEOUT = float(os.getenv("REDIS_CONNECT_TIMEOUT", "5"))

if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [REDIS_URL],
                "capacity": 1500,
                "expiry": 60,
            },
        }
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        }
    }

CHANNELS_ENABLED = os.getenv("CHANNELS_ENABLED", "True").lower() in ("true", "1", "yes")

DATABASES = {}

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 上传大小限制（视频 ingest），默认 200MB
DATA_UPLOAD_MAX_MB = int(os.getenv("DATA_UPLOAD_MAX_MB", "200"))
DATA_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = DATA_UPLOAD_MAX_MEMORY_SIZE

# --- Interpreter service config (from .env) ---
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_MODEL_PATH = os.getenv("WHISPER_MODEL_PATH", str(BASE_DIR / "models" / "faster-whisper-base"))
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_WARMUP = os.getenv("WHISPER_WARMUP", "True").lower() in ("true", "1", "yes")

LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

TTS_VOICE = os.getenv("TTS_VOICE", "zh-CN-XiaoxiaoNeural")

# In-memory session TTL (seconds)
SESSION_TTL = int(os.getenv("SESSION_TTL", "3600"))

# 流式音频分片时长（秒），默认 300ms 低延迟
AUDIO_CHUNK_DURATION_SEC = float(os.getenv("AUDIO_CHUNK_DURATION_SEC", "0.3"))

# 管线超时（秒）
PIPELINE_TIMEOUT_SEC = int(os.getenv("PIPELINE_TIMEOUT_SEC", "120"))

# 口语压缩（ASR 后、翻译前）
SPEECH_COMPRESSOR_ENABLED = os.getenv("SPEECH_COMPRESSOR_ENABLED", "True").lower() in (
    "true",
    "1",
    "yes",
)
SPEECH_COMPRESSOR_MIN_CHARS = int(os.getenv("SPEECH_COMPRESSOR_MIN_CHARS", "12"))

# PPT 上下文最大字符数
PPT_CONTEXT_MAX_CHARS = int(os.getenv("PPT_CONTEXT_MAX_CHARS", "4000"))

# 七牛云 Kodo（可选）
QINIU_ACCESS_KEY = os.getenv("QINIU_ACCESS_KEY", "")
QINIU_SECRET_KEY = os.getenv("QINIU_SECRET_KEY", "")
QINIU_BUCKET = os.getenv("QINIU_BUCKET", "")
QINIU_DOMAIN = os.getenv("QINIU_DOMAIN", "")  # 如 https://cdn.example.com
