"""
API：音频流接收、字幕返回、纠错、TTS。
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

from .utils.asr import transcribe_stream_chunk
from .utils.stream import (
    cleanup_stale_sessions,
    float32_pcm_to_int16,
    get_or_create_session,
    webm_to_pcm,
    wav_bytes_to_pcm,
)
from .utils.translate import translate_with_correction
from .utils.tts import synthesize_speech


def index(request):
    """主页面：麦克风 / 文件上传 / 实时字幕。"""
    return render(request, "index.html")


def _session_id(request) -> str:
    sid = request.headers.get("X-Session-Id") or request.GET.get("session_id")
    if not sid:
        sid = request.POST.get("session_id")
    return sid or str(uuid.uuid4())


@csrf_exempt
@require_http_methods(["POST"])
def api_audio_chunk(request):
    """
    接收音频分片（webm / wav / 原始 pcm）。
    FormData:
      - audio: 文件
      - format: webm | wav | pcm | pcm_float
      - sample_rate: 16000（pcm 时）
      - flush: 1 表示结束并处理剩余缓冲
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

    try:
        if fmt == "webm":
            pcm = webm_to_pcm(raw)
            sample_rate = 16000
        elif fmt == "wav":
            pcm, sample_rate = wav_bytes_to_pcm(raw)
        elif fmt == "pcm_float":
            pcm = float32_pcm_to_int16(raw)
        else:
            pcm = raw
        session.sample_rate = sample_rate
        session.append_chunk(pcm)
    except Exception as e:
        return JsonResponse({"error": f"audio decode failed: {e}"}, status=400)

    segment = None
    if flush:
        segment = session.flush_all()
    else:
        segment = session.take_segment(min_duration_sec=1.2)

    result = {
        "session_id": session_id,
        "asr": {"text": "", "language": "", "is_partial": True},
        "translation": "",
        "corrections": [],
        "subtitle": None,
        "tts_available": False,
    }

    if not segment:
        return JsonResponse(result)

    asr = transcribe_stream_chunk(segment, session.sample_rate)
    result["asr"] = asr

    source_text = asr.get("text", "").strip()
    if not source_text:
        return JsonResponse(result)

    tr = translate_with_correction(source_text, session.history)
    result["translation"] = tr.get("translation", "")
    result["corrections"] = tr.get("corrections", [])
    result["fallback"] = tr.get("fallback", False)

    subtitle = {
        "source": source_text,
        "target": result["translation"],
        "is_partial": asr.get("is_partial", False),
    }
    session.history.append(
        {"source": source_text, "target": result["translation"]}
    )
    result["subtitle"] = subtitle

    return JsonResponse(result)


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
    """
    中文 TTS，返回 MP3 二进制或 base64 JSON。
    Body JSON: {"text": "...", "base64": true}
    """
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
        }
    )
