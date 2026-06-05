from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("status/", views.status_page, name="status_page"),
    path("api/status/", views.api_status, name="api_status"),
    path("api/health/", views.api_health, name="api_health"),
    path("api/session/progress/", views.api_session_progress, name="api_session_progress"),
    path("api/session/control/", views.api_session_control, name="api_session_control"),
    path("api/audio/chunk/", views.api_audio_chunk, name="api_audio_chunk"),
    path("api/upload/", views.api_upload_file, name="api_upload_file"),
    path("api/upload-ppt/", views.api_upload_ppt, name="api_upload_ppt"),
    path("api/upload-terms/", views.api_upload_terms, name="api_upload_terms"),
    path("api/correct/", views.api_correct, name="api_correct"),
    path("api/video/ingest/", views.api_video_ingest, name="api_video_ingest"),
    path("api/demo/script/", views.api_demo_script, name="api_demo_script"),
    path("api/export/subtitles/", views.api_export_subtitles, name="api_export_subtitles"),
    path("api/export/bundle/", views.api_export_bundle, name="api_export_bundle"),
    path("api/history/", views.api_history_list, name="api_history_list"),
    path("api/history/save/", views.api_history_save, name="api_history_save"),
    path("api/history/<str:record_id>/", views.api_history_detail, name="api_history_detail"),
    path("api/history/<str:record_id>/delete/", views.api_history_record, name="api_history_delete"),
    path("api/session/reset/", views.api_reset_session, name="api_reset_session"),
    path("api/tts/", views.api_tts, name="api_tts"),
]
