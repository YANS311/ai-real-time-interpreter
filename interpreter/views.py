"""
API：音频流接收、字幕返回、纠错、TTS、七牛上传。
"""
from __future__ import annotations

import base64
import json
import uuid

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .utils.circuit_breaker import get_asr_breaker, get_llm_breaker
from .utils.pipeline import process_audio_segment
from .utils.qiniu_storage import is_qiniu_configured, upload_bytes
from .utils.stream import (
    cleanup_stale_sessions,
    float32_pcm_to_int16,
    get_or_create_session,
    webm_to_pcm,
    wav_bytes_to_pcm,
)
from .utils.tts import synthesize_speech


def index(request):
    """主页面：麦克风 / 文件上传 / 实时字幕。"""
    return render(request, "index.html")


def _session_id(request) -> str:
    sid = request.headers.get("X-Session-Id") or request.GET.get("session_id")
    if not sid:
        sid = request.POST.get("session_id")
    return sid or str(uuid.uuid4())


def _decode_audio(raw: bytes, fmt: str, sample_rate: int) -> tuple[bytes, int]:
    """统一音频解码入口。"""
    if fmt == "webm":
        return webm_to_pcm(raw), 16000
    if fmt == "wav":
        return wav_bytes_to_pcm(raw)
    if fmt == "pcm_float":
        return float32_pcm_to_int16(raw), sample_rate
    return raw, sample_rate


def _empty_chunk_response(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "asr": {"text": "", "language": "", "is_partial": True},
        "translation": "",
        "corrections": [],
        "subtitle": None,
        "latency_ms": 0,
    }


@csrf_exempt
@require_http_methods(["POST"])
def api_audio_chunk(request):
    """
    接收音频分片（webm / wav / pcm），流式 ASR + 翻译纠错。
    """
    cleanup_stale_sessions(settings.SESSION_TTL)
    session_id = _session_id(request)
    session = get_or_create_session(session_id)

    audio_file = request.FILES.get("audio")
    if not audio_file:
        return JsonResponse({"error": "missing audio"}, status=400)

    raw = audio_file.read()
    fmt = (request.POST.get("format") or "webm").lower()
    sample_rate = int(request.POST.get("sample_rate") or 16000)
    flush = request.POST.get("flush") == "1"
    source_lang = request.POST.get("source_lang") or "auto"

    try:
        pcm, sample_rate = _decode_audio(raw, fmt, sample_rate)
        session.sample_rate = sample_rate
        session.append_chunk(pcm)
    except Exception as e:
        return JsonResponse({"error": f"audio decode failed: {e}"}, status=400)

    segment = session.flush_all() if flush else session.take_segment()
    if not segment:
        return JsonResponse(_empty_chunk_response(session_id))

    if get_asr_breaker().is_open() or get_llm_breaker().is_open():
        return JsonResponse(
            {
                **_empty_chunk_response(session_id),
                "error": "service temporarily unavailable (circuit open)",
                "circuit": {
                    "asr": get_asr_breaker().status(),
                    "llm": get_llm_breaker().status(),
                },
            },
            status=503,
        )

    result = process_audio_segment(
        segment,
        session.sample_rate,
        session.history,
        source_lang=source_lang,
    )
    result["session_id"] = session_id
    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
def api_upload_file(request):
    """
    上传完整音视频文件：可选存七牛云，并返回云端 URL。
    """
    session_id = _session_id(request)
    audio_file = request.FILES.get("file") or request.FILES.get("audio")
    if not audio_file:
        return JsonResponse({"error": "missing file"}, status=400)

    raw = audio_file.read()
    name = audio_file.name or "upload.bin"
    content_type = audio_file.content_type or "application/octet-stream"

    qiniu_result = upload_bytes(raw, name, content_type)
    return JsonResponse(
        {
            "session_id": session_id,
            "filename": name,
            "size": len(raw),
            "qiniu": qiniu_result,
            "qiniu_enabled": is_qiniu_configured(),
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_reset_session(request):
    """清空会话缓冲与历史。"""
    from .utils.stream import _SESSIONS

    session_id = _session_id(request)
    if session_id in _SESSIONS:
        del _SESSIONS[session_id]
    return JsonResponse({"ok": True, "session_id": session_id})


@csrf_exempt
@require_http_methods(["POST"])
def api_tts(request):
    """中文 TTS，返回 MP3 二进制或 base64 JSON。"""
    try:
        body = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        body = {"text": request.POST.get("text", "")}

    text = body.get("text", "").strip()
    if not text:
        return JsonResponse({"error": "empty text"}, status=400)

    audio = synthesize_speech(text)
    if not audio:
        return JsonResponse({"error": "tts failed"}, status=500)

    if body.get("base64"):
        return JsonResponse(
            {
                "audio_base64": base64.b64encode(audio).decode("ascii"),
                "content_type": "audio/mpeg",
            }
        )

    return HttpResponse(audio, content_type="audio/mpeg")


@require_GET
def api_health(request):
    """健康检查。"""
    return JsonResponse(
        {
            "status": "ok",
            "whisper_model": settings.WHISPER_MODEL,
            "llm_configured": bool(settings.LLM_API_KEY),
            "qiniu_configured": is_qiniu_configured(),
            "chunk_duration_ms": int(settings.AUDIO_CHUNK_DURATION_SEC * 1000),
            "circuit": {
                "asr": get_asr_breaker().status(),
                "llm": get_llm_breaker().status(),
            },
        }
    )
