# AI 同声传译助手

七牛云暑期实训项目：基于 **Django 4.2** 的实时同声传译 Web 应用。

- 麦克风实时收音 / 本地音视频文件上传
- **faster-whisper** 流式语音转文字
- 大模型实时中文字幕 + **AI 自动纠错** 历史字幕
- **Edge-TTS** 中文语音播报
- 原生 HTML + JavaScript 前端，字幕实时滚动

## 技术栈

| 组件 | 说明 |
|------|------|
| Python 3.10+ | 运行环境 |
| Django 4.2 | Web 框架 |
| faster-whisper | 流式 ASR |
| OpenAI 兼容 API | 翻译与纠错 |
| Edge-TTS | 中文合成 |
| pydub + ffmpeg | 音频格式转换 |

## 快速开始

### 1. 环境准备

```bash
# 需要 ffmpeg（pydub 转码）
# macOS
brew install ffmpeg

cd ai-real-time-interpreter
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置密钥

```bash
cp .env.example .env
# 编辑 .env，填入 LLM_API_KEY（OpenAI / DeepSeek / 七牛等兼容接口均可）
```

### 3. 启动服务

```bash
python manage.py runserver
```

浏览器打开：<http://127.0.0.1:8000/>

首次运行会自动下载 Whisper 模型（由 `WHISPER_MODEL` 决定，默认 `base`）。

## 项目结构

```
ai-real-time-interpreter/
├── manage.py
├── requirements.txt
├── README.md
├── .env.example
├── core/                 # Django 配置
├── interpreter/          # 主业务
│   ├── views.py          # 音频分片、字幕、TTS API
│   ├── urls.py
│   ├── utils/
│   │   ├── asr.py        # Whisper 识别
│   │   ├── translate.py  # 翻译 + 纠错
│   │   ├── tts.py        # Edge-TTS
│   │   └── stream.py     # 音频流缓冲
│   └── templates/
│       └── index.html    # 前端页面
```

## API 说明

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页面 |
| `/api/health/` | GET | 健康检查 |
| `/api/audio/chunk/` | POST | 上传音频分片，返回 ASR + 翻译 + 纠错 |
| `/api/session/reset/` | POST | 清空会话 |
| `/api/tts/` | POST | 中文 TTS，Body: `{"text":"..."}` |

请求头可带 `X-Session-Id` 保持同一会话上下文。

## 推送到 GitHub

```bash
git add .
git commit -m "init: Django AI 同声传译项目完成"
git remote add origin https://github.com/YANS311/ai-real-time-interpreter.git
git push -u origin main
```

## 常见问题

1. **没有翻译，只有 `[待翻译]`**  
   请在 `.env` 中配置 `LLM_API_KEY`。

2. **音频解码失败**  
   确认已安装 `ffmpeg`，且 `pydub` 可正常调用。

3. **Whisper 较慢**  
   可将 `WHISPER_MODEL` 改为 `tiny`；有 GPU 时设置 `WHISPER_DEVICE=cuda`。

## License

MIT
