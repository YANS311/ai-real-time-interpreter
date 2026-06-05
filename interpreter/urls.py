from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/health/", views.api_health, name="api_health"),
    path("api/audio/chunk/", views.api_audio_chunk, name="api_audio_chunk"),
    path("api/upload/", views.api_upload_file, name="api_upload_file"),
    path("api/video/ingest/", views.api_video_ingest, name="api_video_ingest"),
    path("api/export/subtitles/", views.api_export_subtitles, name="api_export_subtitles"),
    path("api/session/reset/", views.api_reset_session, name="api_reset_session"),
    path("api/tts/", views.api_tts, name="api_tts"),
]
