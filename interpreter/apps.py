import sys
import threading

from django.apps import AppConfig


class InterpreterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "interpreter"
    verbose_name = "AI 同声传译"

    def ready(self) -> None:
        from django.conf import settings

        if not getattr(settings, "WHISPER_WARMUP", True):
            return
        if any(cmd in sys.argv for cmd in ("migrate", "check", "collectstatic", "test", "shell")):
            return

        from .utils.asr import warm_up_whisper

        threading.Thread(target=warm_up_whisper, daemon=True, name="whisper-warmup").start()
