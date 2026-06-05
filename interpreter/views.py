"""
API：音频流接收、字幕返回、纠错、TTS、七牛上传、视频 BGM 处理、字幕导出。
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

from .utils.audio_process import is_spleeter_available
from .utils.bgm import is_video_file, prepare_media_for_interpretation
from .utils.circuit_breaker import get_asr_breaker, get_llm_breaker
from .utils.export import subtitles_to_srt, subtitles_to_txt
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


def _empty_chunk_response(session_id: str, bgm_info: dict | None = None) -> dict:
    resp = {
        "session_id": session_id,
        "asr": {"text": "", "language": "", "is_partial": True},
        "translation": "",
        "corrections": [],
        "subtitle": None,
        "latency_ms": 0,
        "file_eof": False,
    }
    if bgm_info:
        resp["bgm"] = bgm_info
    return resp


def _process_segment(
    session,
    segment: bytes,
    source_lang: str,
) -> dict:
    """对一段 PCM 执行 ASR + 翻译管线。"""
    seg_dur = len(segment) / (session.sample_rate * 2)
    start_sec = session.processed_duration_sec

    if get_asr_breaker().is_open() or get_llm_breaker().is_open():
        return {
            **_empty_chunk_response(session.session_id, session.bgm_info or None),
            "error": "service temporarily unavailable (circuit open)",
            "circuit": {
                "asr": get_asr_breaker().status(),
                "llm": get_llm_breaker().status(),
            },
        }

    result = process_audio_segment(
        segment,
        session.sample_rate,
        session.history,
        source_lang=source_lang,
        segment_start_sec=start_sec,
        segment_duration_sec=seg_dur,
    )
    session.advance_duration(segment)
    result["session_id"] = session.session_id
    if session.bgm_info:
        result["bgm"] = session.bgm_info
    return result


@csrf_exempt
@require_http_methods(["POST"])
def api_audio_chunk(request):
    """
    接收音频分片（webm / wav / pcm），或 pull_processed=1 拉取服务端预处理缓冲。
    """
    cleanup_stale_sessions(settings.SESSION_TTL)
    session_id = _session_id(request)
    session = get_or_create_session(session_id)
    source_lang = request.POST.get("source_lang") or "auto"
    flush = request.POST.get("flush") == "1"
    pull_processed = request.POST.get("pull_processed") == "1"

    if pull_processed:
        if flush:
            segment = session.flush_processed()
        else:
            segment = session.take_processed_segment()
            # 剩余不足一片时自动取出，避免尾段丢失
            if not segment and session.processed_file_buffer:
                segment = session.flush_processed()
        if not segment:
            resp = _empty_chunk_response(session_id, session.bgm_info or None)
            resp["file_eof"] = True
            return JsonResponse(resp)
        result = _process_segment(session, segment, source_lang)
        result["file_eof"] = not bool(session.processed_file_buffer)
        return JsonResponse(result)

    audio_file = request.FILES.get("audio")
    if not audio_file:
        return JsonResponse({"error": "missing audio"}, status=400)

    raw = audio_file.read()
    fmt = (request.POST.get("format") or "webm").lower()
    sample_rate = int(request.POST.get("sample_rate") or 16000)
    separate_bgm = request.POST.get("separate_bgm") == "1"
    denoise = request.POST.get("denoise", "1") == "1"

    try:
        pcm, sample_rate = _decode_audio(raw, fmt, sample_rate)
        if (separate_bgm or denoise) and len(pcm) > 3200:
            from pydub import AudioSegment

            from .utils.audio_process import separate_vocals

            seg = AudioSegment(
                pcm, sample_width=2, frame_rate=sample_rate, channels=1
            )
            method = request.POST.get("separation_method", "fast")
            vocal, _ = separate_vocals(
                seg, method=method, denoise=denoise, denoise_strength=0.5
            )
            pcm = (
                vocal.set_frame_rate(16000)
                .set_channels(1)
                .set_sample_width(2)
                .raw_data
            )
            sample_rate = 16000
        session.sample_rate = sample_rate
        session.append_chunk(pcm)
    except Exception as e:
        return JsonResponse({"error": f"audio decode failed: {e}"}, status=400)

    segment = session.flush_all() if flush else session.take_segment()
    if not segment:
        return JsonResponse(_empty_chunk_response(session_id))

    result = _process_segment(session, segment, source_lang)
    return JsonResponse(result)


@csrf_exempt
@require_http_methods(["POST"])
def api_video_ingest(request):
    """
    视频/音频文件服务端预处理：提取音轨 → BGM 检测 → 人声分离 → 载入会话缓冲。

    FormData:
      - file: 音视频文件
      - separate_bgm: 1 启用人声分离（默认视频自动开启）
    """
    session_id = _session_id(request)
    session = get_or_create_session(session_id)

    media_file = request.FILES.get("file") or request.FILES.get("audio")
    if not media_file:
        return JsonResponse({"error": "missing file"}, status=400)

    raw = media_file.read()
    name = media_file.name or "upload.mp4"
    separate_bgm = request.POST.get("separate_bgm", "1") == "1"
    denoise = request.POST.get("denoise", "1") == "1"
    separation_method = request.POST.get("separation_method", "auto")
    if is_video_file(name):
        separate_bgm = request.POST.get("separate_bgm", "1") != "0"

    try:
        pcm, sample_rate, bgm_info = prepare_media_for_interpretation(
            raw,
            name,
            separate_bgm=separate_bgm,
            denoise=denoise,
            separation_method=separation_method,
        )
        session.load_processed_file(pcm, sample_rate, bgm_info)
    except Exception as e:
        return JsonResponse({"error": f"media process failed: {e}"}, status=400)

    qiniu_result = upload_bytes(raw, name, media_file.content_type or "application/octet-stream")

    return JsonResponse(
        {
            "session_id": session_id,
            "filename": name,
            "bgm": bgm_info,
            "pcm_duration_sec": round(len(pcm) / (sample_rate * 2), 2),
            "qiniu": qiniu_result,
            "ready": True,
            "message": "预处理完成，请开始拉取分片 (pull_processed=1)",
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_upload_file(request):
    """上传完整音视频文件：可选存七牛云，并返回云端 URL。"""
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
@require_http_methods(["GET", "POST"])
def api_export_subtitles(request):
    """
    导出字幕 SRT / TXT。
    GET/POST: format=srt|txt, 可选 body JSON {"items": [...]}，否则用会话历史。
    """
    session_id = _session_id(request)
    session = get_or_create_session(session_id)
    fmt = (request.GET.get("format") or request.POST.get("format") or "srt").lower()

    items = session.history
    if request.method == "POST" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            if body.get("items"):
                items = body["items"]
        except json.JSONDecodeError:
            pass

    if not items:
        return JsonResponse({"error": "no subtitles"}, status=404)

    if fmt == "txt":
        content = subtitles_to_txt(items)
        return HttpResponse(content, content_type="text/plain; charset=utf-8")

    content = subtitles_to_srt(items)
    return HttpResponse(
        content,
        content_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'},
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
            "bgm_separation": True,
            "spleeter_available": is_spleeter_available(),
            "circuit": {
                "asr": get_asr_breaker().status(),
                "llm": get_llm_breaker().status(),
            },
        }
    )
