"""
API：音频流接收、字幕返回、纠错、TTS、七牛上传、视频 BGM 处理、字幕导出。
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from .utils.audio_process import is_spleeter_available
from .utils.demo import load_demo_script
from .utils.export_bundle import build_export_bundle
from .utils.glossary import load_default_glossary, merge_glossary, parse_client_glossary
from .utils.ppt_context import normalize_ppt_text, parse_upload_payload, summarize_context
from .utils.bgm import is_video_file, prepare_media_for_interpretation
from .utils.circuit_breaker import get_asr_breaker, get_llm_breaker
from .utils.history_store import (
    delete_record,
    get_record,
    items_to_plain_text,
    list_records,
    save_record,
)
from .utils.subtitle_export import export_subtitles
from .utils.video_extract import extract_audio_from_media, is_supported_media
from .utils.correction import apply_session_correction, history_payload
from .utils.pipeline import process_audio_segment
from .utils.qiniu_storage import is_qiniu_configured
from .utils.ws_events import push_chunk_result, push_session_event
from .utils.stream import (
    cleanup_stale_sessions,
    delete_session,
    float32_pcm_to_int16,
    get_or_create_session,
    save_session,
    webm_to_pcm,
    wav_bytes_to_pcm,
)
from .utils.session_store import list_sessions
from .utils.redis_client import redis_status
from .utils.tts import synthesize_speech


def _export_filename(stem: str, ext: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stem}_{stamp}.{ext}"


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


def _sync_session_glossary(session, request) -> None:
    """从请求更新会话术语表。"""
    raw = request.POST.get("glossary") or request.headers.get("X-Glossary")
    if raw:
        session.glossary = merge_glossary(
            load_default_glossary(),
            parse_client_glossary(raw),
        )
    elif not session.glossary:
        session.glossary = load_default_glossary()


def _sync_session_ppt_context(session, request) -> None:
    """从请求更新会话 PPT 上下文。"""
    raw = (
        request.POST.get("ppt_context")
        or request.headers.get("X-Ppt-Context")
        or request.headers.get("X-PPT-Context")
    )
    if raw:
        session.ppt_context = normalize_ppt_text(raw)
        session.ppt_context_meta = {"source_type": "inline"}


def _sync_session_chunk_duration(session, request) -> None:
    """从请求同步 ASR 分片时长（秒）。"""
    raw = request.POST.get("chunk_duration") or request.POST.get("chunk_duration_sec")
    if raw:
        try:
            session.chunk_duration_sec = max(0.1, min(3.0, float(raw)))
            return
        except (TypeError, ValueError):
            pass
    mode = (request.POST.get("accuracy_mode") or "").lower()
    if mode == "high":
        session.chunk_duration_sec = 3.0
    elif mode == "low":
        session.chunk_duration_sec = 0.5


def _upload_too_large_response() -> JsonResponse:
    return JsonResponse(
        {
            "error": f"file too large (max {settings.DATA_UPLOAD_MAX_MB}MB)",
            "max_upload_mb": settings.DATA_UPLOAD_MAX_MB,
        },
        status=413,
    )


def _check_upload_size(request) -> JsonResponse | None:
    """在上传前检查 Content-Length，返回错误响应或 None。"""
    raw_len = request.META.get("CONTENT_LENGTH")
    if not raw_len:
        return None
    try:
        if int(raw_len) > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
            return _upload_too_large_response()
    except (TypeError, ValueError):
        pass
    return None


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
    compress_speech_enabled: bool = True,
) -> dict:
    """对一段 PCM 执行 ASR + 句子断句 + 翻译管线。"""
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
        bgm_info=session.bgm_info or None,
        glossary=session.glossary or None,
        ppt_context=session.ppt_context or "",
        compress_speech_enabled=compress_speech_enabled,
        speaker_tracker=session.speaker_tracker,
        sentence_buffer=session.sentence_buffer,
        sentence_start_sec=session.sentence_start_sec,
    )

    # 更新 session 的句子缓冲状态
    if result.get("sentence_flushed"):
        session.sentence_buffer = ""
        session.sentence_start_sec = 0.0
    else:
        session.sentence_buffer = result.get("sentence_buffer", "")
        if not session.sentence_start_sec:
            session.sentence_start_sec = start_sec

    session.advance_duration(segment)
    session.last_asr_end_sec = start_sec + seg_dur
    result["session_id"] = session.session_id
    if session.bgm_info:
        result["bgm"] = session.bgm_info
    if session.total_pcm_bytes:
        result["progress"] = session.playback_progress()
    push_chunk_result(session.session_id, result)
    save_session(session)
    result["chunk_duration_ms"] = int(session.effective_chunk_duration() * 1000)
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
    _sync_session_glossary(session, request)
    _sync_session_ppt_context(session, request)
    _sync_session_chunk_duration(session, request)
    session.speaker_tracker.enabled = request.POST.get("speaker_labels", "1") == "1"
    source_lang = request.POST.get("source_lang") or "auto"
    flush = request.POST.get("flush") == "1"
    pull_processed = request.POST.get("pull_processed") == "1"
    compress_speech_enabled = request.POST.get("compress_speech", "1") == "1"

    if pull_processed:

        if session.paused:
            resp = _empty_chunk_response(session_id, session.bgm_info or None)
            resp["paused"] = True
            resp["progress"] = session.playback_progress()
            return JsonResponse(resp)

        if flush:
            segment = session.flush_processed()
        else:
            segment = session.take_processed_segment()
            if not segment and session._processed_remaining() > 1600:
                segment = session.flush_processed()
        if not segment:
            resp = _empty_chunk_response(session_id, session.bgm_info or None)
            resp["file_eof"] = session._processed_remaining() == 0
            resp["progress"] = session.playback_progress()
            return JsonResponse(resp)
        result = _process_segment(
            session, segment, source_lang, compress_speech_enabled=compress_speech_enabled
        )
        result["file_eof"] = session._processed_remaining() == 0
        result["progress"] = session.playback_progress()
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
        # 麦克风实时分片不做 Spleeter 人声分离（过重，会导致严重卡顿）
        if separate_bgm and not pull_processed:
            separate_bgm = False
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
        save_session(session)
    except Exception as e:
        return JsonResponse({"error": f"audio decode failed: {e}"}, status=400)

    segment = session.flush_all() if flush else session.take_segment()
    if not segment:
        return JsonResponse(_empty_chunk_response(session_id))

    result = _process_segment(
        session, segment, source_lang, compress_speech_enabled=compress_speech_enabled
    )
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
    try:
        return _api_video_ingest_inner(request)
    except Exception as e:
        import logging, traceback
        logging.getLogger(__name__).error("video ingest failed: %s\n%s", e, traceback.format_exc())
        return JsonResponse({"error": f"video ingest failed: {e}"}, status=400)


def _api_video_ingest_inner(request):
    session_id = _session_id(request)
    session = get_or_create_session(session_id)

    too_large = _check_upload_size(request)
    if too_large:
        return too_large

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

    if not is_supported_media(name):
        return JsonResponse(
            {"error": f"unsupported format: {name}. use MP4/MOV/AVI or common audio"},
            status=400,
        )

    try:
        _, wav_bytes, _, extract_meta = extract_audio_from_media(raw, name)
        pcm, sample_rate, bgm_info = prepare_media_for_interpretation(
            raw,
            name,
            separate_bgm=separate_bgm,
            denoise=denoise,
            separation_method=separation_method,
        )
        session.load_processed_file(pcm, sample_rate, bgm_info)
        session.speaker_tracker.reset()
        bgm_info["extract"] = extract_meta
    except Exception as e:
        import logging, traceback
        logging.getLogger(__name__).error("media process failed: %s\n%s", e, traceback.format_exc())
        return JsonResponse({"error": f"media process failed: {e}"}, status=400)

    save_session(session)

    return JsonResponse(
        {
            "session_id": session_id,
            "filename": name,
            "video": extract_meta,
            "bgm": bgm_info,
            "wav_size": len(wav_bytes),
            "pcm_duration_sec": round(len(pcm) / (sample_rate * 2), 2),
            "ready": True,
            "message": "视频音频提取完成，已进入流式识别队列",
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_upload_file(request):
    """上传完整音视频文件。"""
    too_large = _check_upload_size(request)
    if too_large:
        return too_large

    session_id = _session_id(request)
    audio_file = request.FILES.get("file") or request.FILES.get("audio")
    if not audio_file:
        return JsonResponse({"error": "missing file"}, status=400)

    raw = audio_file.read()
    name = audio_file.name or "upload.bin"

    return JsonResponse(
        {
            "session_id": session_id,
            "filename": name,
            "size": len(raw),
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_upload_ppt(request):
    """
    上传 PPT / 演讲上下文（文本或截图 + 可选说明）。

    JSON: {"text": "..."}
    FormData: text/context 字段，或 file（PNG/JPG 截图）+ 可选 text 补充说明
    """
    session_id = _session_id(request)
    session = get_or_create_session(session_id)

    body: dict = {}
    if request.body and request.content_type and "json" in request.content_type:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid json"}, status=400)

    text = parse_upload_payload(body, request.POST)
    meta = {"source_type": "text", "source_filename": ""}

    image = request.FILES.get("file") or request.FILES.get("ppt") or request.FILES.get("image")
    if image:
        meta["source_filename"] = image.name or "ppt.png"
        meta["source_type"] = "image"
        meta["content_type"] = image.content_type or ""
        meta["size"] = image.size
        if not text:
            text = (
                f"[PPT 截图已上传: {meta['source_filename']}。"
                "请在 text 字段补充幻灯片标题、章节与关键术语，以便翻译参考。]"
            )

    if not text:
        return JsonResponse({"error": "missing text or image"}, status=400)

    session.ppt_context = normalize_ppt_text(text)
    session.ppt_context_meta = meta
    save_session(session)

    summary = summarize_context(session.ppt_context, session.ppt_context_meta)
    return JsonResponse(
        {
            "ok": True,
            "session_id": session_id,
            "ppt_context": summary,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_upload_terms(request):
    """上传术语表 JSON，合并到当前会话。"""
    session_id = _session_id(request)
    session = get_or_create_session(session_id)

    body: dict = {}
    if request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            body = {}

    raw = body.get("terms") or body.get("glossary") or request.POST.get("glossary")
    if not raw:
        return JsonResponse({"error": "missing terms"}, status=400)

    parsed = parse_client_glossary(raw)
    session.glossary = merge_glossary(load_default_glossary(), parsed)
    save_session(session)
    return JsonResponse(
        {
            "ok": True,
            "session_id": session_id,
            "count": len(session.glossary),
            "glossary": session.glossary,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def api_correct(request):
    """
    手动或 LLM 修正字幕行。

    JSON:
      {"session_id", "index": 0, "new_text": "修正译文", "mode": "manual"|"llm"}
    """
    try:
        body = json.loads(request.body.decode("utf-8")) if request.body else {}
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    session_id = body.get("session_id") or _session_id(request)
    session = get_or_create_session(session_id)
    index = body.get("index")
    mode = (body.get("mode") or "manual").lower()
    new_text = (body.get("new_text") or "").strip()

    try:
        if index is None:
            raise ValueError("missing index")
        payload = apply_session_correction(
            session,
            int(index),
            new_text=new_text,
            mode=mode,
        )
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except Exception as e:
        return JsonResponse({"error": f"correction failed: {e}"}, status=500)

    save_session(session)
    push_session_event(session_id, "correction", payload)
    return JsonResponse(payload)


@require_GET
def api_demo_script(request):
    """返回内置样例字幕脚本；Whisper 未就绪时降级为 mock 文本流。"""
    from .utils.asr import warm_up_whisper

    whisper_ready = warm_up_whisper()
    script = load_demo_script()
    if not whisper_ready:
        script = {
            "title": "演示（Mock 模式）",
            "lines": [
                {"source": "Hello, welcome to the demo.", "target": "你好，欢迎来到演示。", "delay_ms": 1500},
                {"source": "This is a real-time interpreter.", "target": "这是一个实时同传系统。", "delay_ms": 1500},
                {"source": "It uses Whisper for speech recognition.", "target": "它使用 Whisper 进行语音识别。", "delay_ms": 1500},
                {"source": "And a large language model for translation.", "target": "并使用大语言模型进行翻译。", "delay_ms": 1500},
                {"source": "Thank you for watching.", "target": "感谢观看。", "delay_ms": 1500},
            ],
            "whisper_ready": False,
        }
    else:
        script["whisper_ready"] = True
    return JsonResponse(script)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_export_bundle(request):
    """打包导出 SRT + TXT + JSON。"""
    session_id = _session_id(request)
    session = get_or_create_session(session_id)
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
    meta = {
        "session_id": session_id,
        "glossary": session.glossary,
        "ppt_context": summarize_context(session.ppt_context, session.ppt_context_meta),
        "bgm": session.bgm_info,
    }
    data = build_export_bundle(items, meta=meta)
    return HttpResponse(
        data,
        content_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{_export_filename("subtitles_bundle", "zip")}"',
        },
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def api_export_subtitles(request):
    """
    导出字幕 SRT / VTT / TXT。
    GET/POST: format=srt|vtt|txt, 可选 body JSON {"items": [...]}，否则用会话历史。
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

    content = export_subtitles(items, fmt)
    if fmt == "txt":
        return HttpResponse(
            content,
            content_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{_export_filename("subtitles", "txt")}"',
            },
        )
    if fmt == "vtt":
        return HttpResponse(
            content,
            content_type="text/vtt; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{_export_filename("subtitles", "vtt")}"',
            },
        )
    return HttpResponse(
        content,
        content_type="application/x-subrip; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{_export_filename("subtitles", "srt")}"',
        },
    )


@csrf_exempt
@require_http_methods(["GET"])
def api_session_progress(request):
    """视频/文件同传播放进度。"""
    session_id = _session_id(request)
    session = get_or_create_session(session_id)
    return JsonResponse({"session_id": session_id, **session.playback_progress()})


@csrf_exempt
@require_http_methods(["POST"])
def api_session_control(request):
    """
    控制视频同传：pause / resume / seek。
    Body JSON: {"action": "pause"|"resume"|"seek", "position": 0.0~1.0}
    """
    try:
        body = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        body = {}

    session_id = body.get("session_id") or _session_id(request)
    session = get_or_create_session(session_id)
    action = (body.get("action") or request.POST.get("action") or "").lower()

    if action == "pause":
        session.paused = True
    elif action == "resume":
        session.paused = False
    elif action == "seek":
        session.seek_processed(float(body.get("position", 0)))
    elif action == "stop":
        session.paused = False
        session.processed_read_offset = len(session.processed_file_pcm)
    else:
        return JsonResponse({"error": "unknown action"}, status=400)

    save_session(session)
    resp = {
        "ok": True,
        "action": action,
        "session_id": session_id,
        **session.playback_progress(),
    }
    if action == "seek":
        resp["history"] = history_payload(session.history)
    return JsonResponse(resp)


@csrf_exempt
@require_http_methods(["POST"])
def api_reset_session(request):
    """清空会话缓冲与历史。"""
    session_id = _session_id(request)
    delete_session(session_id)
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


@csrf_exempt
@require_http_methods(["GET"])
def api_history_list(request):
    """列出已保存的同传历史（摘要）。"""
    limit = int(request.GET.get("limit") or 30)
    return JsonResponse({"records": list_records(limit=limit)})


@csrf_exempt
@require_http_methods(["GET"])
def api_history_detail(request, record_id: str):
    """获取单条历史记录详情。"""
    record = get_record(record_id)
    if not record:
        return JsonResponse({"error": "not found"}, status=404)
    record["plain_text"] = items_to_plain_text(record.get("items") or [])
    return JsonResponse(record)


@csrf_exempt
@require_http_methods(["POST", "DELETE"])
def api_history_record(request, record_id: str):
    """删除历史记录。"""
    if request.method != "DELETE":
        return JsonResponse({"error": "use DELETE"}, status=405)
    if delete_record(record_id):
        return JsonResponse({"ok": True})
    return JsonResponse({"error": "not found"}, status=404)


@csrf_exempt
@require_http_methods(["POST"])
def api_history_save(request):
    """
    保存当前会话或提交的字幕为历史记录。
    Body JSON: {"session_id", "title", "items", "meta"}
    """
    try:
        body = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        body = {}

    session_id = body.get("session_id") or _session_id(request)
    session = get_or_create_session(session_id)
    items = body.get("items") or session.history
    if not items:
        return JsonResponse({"error": "no subtitles to save"}, status=400)

    meta = body.get("meta") or {}
    if session.bgm_info:
        meta["bgm"] = session.bgm_info

    saved = save_record(
        session_id=session_id,
        items=items,
        meta=meta,
        title=body.get("title"),
    )
    return JsonResponse({"ok": True, "record": saved})


def _system_status_payload() -> dict:
    """汇总系统运行状态。"""
    sessions = list_sessions(limit=500)
    active_file = sum(1 for s in sessions if s.total_pcm_bytes)
    paused = sum(1 for s in sessions if s.paused)
    history_count = len(list_records(limit=500))
    redis_info = redis_status()
    channel_layer = "redis" if redis_info["enabled"] else "memory"
    session_backend = "redis" if redis_info["enabled"] and settings.REDIS_SESSIONS_ENABLED else "memory"
    return {
        "status": "ok",
        "whisper_model": settings.WHISPER_MODEL,
        "whisper_device": settings.WHISPER_DEVICE,
        "llm_configured": bool(settings.LLM_API_KEY),
        "llm_model": settings.LLM_MODEL,
        "qiniu_configured": is_qiniu_configured(),
        "chunk_duration_ms": int(settings.AUDIO_CHUNK_DURATION_SEC * 1000),
        "speech_compressor_enabled": getattr(settings, "SPEECH_COMPRESSOR_ENABLED", True),
        "channels_enabled": getattr(settings, "CHANNELS_ENABLED", True),
        "websocket_path": "/ws/interpreter/<session_id>/",
        "redis": redis_info,
        "channel_layer": channel_layer,
        "session_backend": session_backend,
        "bgm_separation": True,
        "spleeter_available": is_spleeter_available(),
        "circuit": {
            "asr": get_asr_breaker().status(),
            "llm": get_llm_breaker().status(),
        },
        "sessions": {
            "active": len(sessions),
            "with_processed_file": active_file,
            "paused": paused,
        },
        "history_records": history_count,
        "max_upload_mb": settings.DATA_UPLOAD_MAX_MB,
    }


@require_GET
def status_page(request):
    """系统状态监控页。"""
    return render(request, "status.html", {"status": _system_status_payload()})


@require_GET
def api_status(request):
    """系统状态 JSON。"""
    return JsonResponse(_system_status_payload())


@require_GET
def api_health(request):
    """健康检查。"""
    payload = _system_status_payload()
    return JsonResponse(
        {
            "status": payload["status"],
            "whisper_model": payload["whisper_model"],
            "llm_configured": payload["llm_configured"],
            "qiniu_configured": payload["qiniu_configured"],
            "chunk_duration_ms": payload["chunk_duration_ms"],
            "bgm_separation": payload["bgm_separation"],
            "spleeter_available": payload["spleeter_available"],
            "circuit": payload["circuit"],
            "redis_connected": payload["redis"]["connected"],
            "session_backend": payload["session_backend"],
        }
    )
