"""
翻译模块：Hy-MT2 本地 GPU 翻译（带上下文），Google Translate 降级。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# Hy-MT2 模型（懒加载，线程安全）
_model = None
_tokenizer = None
_model_lock = threading.Lock()


def _get_hy_mt2():
    global _model, _tokenizer
    if _model is not None:
        return _model, _tokenizer
    with _model_lock:
        if _model is not None:
            return _model, _tokenizer
        try:
            import os
            os.environ.setdefault("HF_ENDPOINT", "https://huggingface.co")
            from transformers import AutoModelForCausalLM, AutoTokenizer
            import torch

            model_id = "tencent/Hy-MT2-1.8B"
            logger.info("Loading Hy-MT2-1.8B translation model...")
            _tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
            _model = AutoModelForCausalLM.from_pretrained(
                model_id,
                dtype=torch.float16,
                device_map="auto",
                trust_remote_code=True,
            )
            logger.info("Hy-MT2-1.8B loaded on %s", _model.device)
            return _model, _tokenizer
        except Exception as e:
            logger.exception("Failed to load Hy-MT2: %s", e)
            _model = None
            _tokenizer = None
            return None, None


def _hy_mt2_translate(text: str, history: list[dict] | None = None) -> str:
    """Hy-MT2 本地 GPU 翻译，带历史上下文。"""
    model, tokenizer = _get_hy_mt2()
    if model is None:
        return ""

    import torch

    # 构建带上下文的消息
    system_msg = "你是同声传译员。将英文翻译为简洁口语化的中文，保持上下文连贯。"
    messages = [{"role": "system", "content": system_msg}]
    if history:
        for h in history[-3:]:
            if h.get("source") and h.get("target"):
                messages.append({"role": "user", "content": h["source"]})
                messages.append({"role": "assistant", "content": h["target"]})
    messages.append({"role": "user", "content": text})

    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to(model.device)

    try:
        with torch.no_grad():
            outputs = model.generate(
                input_ids=inputs["input_ids"],
                max_new_tokens=100,
                do_sample=False,
            )
        resp = tokenizer.decode(
            outputs[0][inputs["input_ids"].shape[1]:],
            skip_special_tokens=True,
        )
        return resp.strip()
    except Exception as e:
        logger.warning("Hy-MT2 translate failed: %s", e)
        return ""


# Google Translate 降级
_gt_lock = threading.Lock()
_gt_instance = None


def _get_gt():
    global _gt_instance
    if _gt_instance is not None:
        return _gt_instance
    with _gt_lock:
        if _gt_instance is not None:
            return _gt_instance
        try:
            from deep_translator import GoogleTranslator
            _gt_instance = GoogleTranslator(source="en", target="zh-CN")
            return _gt_instance
        except ImportError:
            return None


def _gt_translate(text: str) -> str:
    gt = _get_gt()
    if gt is None:
        return ""
    try:
        return gt.translate(text) or ""
    except Exception as e:
        logger.warning("Google Translate failed: %s", e)
        return ""


def apply_corrections_to_history(
    history: list[dict],
    corrections: list[dict],
) -> list[dict]:
    """将纠错写回会话历史。"""
    applied = []
    for c in corrections or []:
        idx = c.get("index")
        if idx is None or idx < 0 or idx >= len(history):
            continue
        if c.get("source"):
            history[idx]["source"] = c["source"]
        if c.get("target"):
            history[idx]["target"] = c["target"]
        history[idx]["corrected"] = True
        applied.append(c)
    return applied


def translate_with_correction(
    source_text: str,
    history: list[dict],
    source_lang: str = "auto",
    glossary: dict[str, str] | None = None,
    ppt_context: str = "",
) -> dict[str, Any]:
    """翻译：Hy-MT2 优先（带上下文），Google 降级。"""
    source_text = (source_text or "").strip()
    if not source_text:
        return {"translation": "", "corrections": [], "fallback": False}

    # Hy-MT2 本地翻译（带上下文）
    translation = _hy_mt2_translate(source_text, history)

    # 降级到 Google Translate
    if not translation:
        translation = _gt_translate(source_text)

    if translation:
        return {
            "translation": translation,
            "corrections": [],
            "fallback": False,
        }

    return {
        "translation": f"[待翻译] {source_text}",
        "corrections": [],
        "fallback": True,
    }
