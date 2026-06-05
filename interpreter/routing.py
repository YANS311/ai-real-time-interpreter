from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(
        r"ws/interpreter/(?P<session_id>[0-9a-fA-F-]{8,})/?$",
        consumers.InterpreterConsumer.as_asgi(),
    ),
]
