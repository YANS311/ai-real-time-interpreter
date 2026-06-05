# AI 同声传译助手

七牛云暑期实训 · 第三批次题目二：**AI 同声传译助手**

基于 **Python 3.10 + Django 4.2** 的实时同声传译 Web 应用，支持 **Win / macOS / Linux**。

## 功能清单

| 功能 | 状态 |
|------|------|
| 麦克风实时收音同传 | ✅ |
| 本地音视频文件上传同传 | ✅ |
| 流式 Whisper ASR（300ms 分片） | ✅ |
| 大模型实时中文字幕 | ✅ |
| AI 自动修正历史识别/翻译 | ✅ |
| Edge-TTS 中文语音播报 | ✅ |
| 字幕实时滚动（深色面板） | ✅ |
| 延迟指标 / 状态指示器 | ✅ |
| 多语种源语言（英/日/韩→中文） | ✅ |
| 七牛云 Kodo 音视频云端存储 | ✅（需配置密钥） |
| 视频 BGM 检测 + 人声分离 | ✅ |
| SRT 字幕导出 | ✅ |
| 异常熔断（ASR/LLM） | ✅ |

## 技术栈

- **后端**：Django 4.2、faster-whisper、httpx（LLM）、edge-tts、pydub
- **前端**：HTML + 原生 JavaScript（无框架）
- **存储**：七牛云 Kodo（可选）
- **依赖**：ffmpeg（音频转码，三平台均需安装）

## 快速部署（Win / Mac / Linux）

### 1. 克隆与虚拟环境

```bash
git clone https://github.com/YANS311/ai-real-time-interpreter.git
cd ai-real-time-interpreter
python -m venv venv
```

**激活虚拟环境：**

```bash
# macOS / Linux
source venv/bin/activate

# Windows (cmd)
venv\Scripts\activate.bat

# Windows (PowerShell)
venv\Scripts\Activate.ps1
```

```bash
pip install -r requirements.txt
```

### 2. 安装 ffmpeg

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt update && sudo apt install -y ffmpeg

# Windows (choco)
choco install ffmpeg
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少配置：

- `LLM_API_KEY` — 翻译与纠错（OpenAI / DeepSeek / 七牛兼容接口）
- `QINIU_ACCESS_KEY` / `QINIU_SECRET_KEY` / `QINIU_BUCKET` / `QINIU_DOMAIN` — 七牛加分项（可选）

### 4. 启动

```bash
python manage.py runserver
```

浏览器打开：<http://127.0.0.1:8000/>

## Demo 演示说明

1. 点击 **「开始麦克风同传」**，对着麦克风说英文/日文，观察实时中文字幕与延迟（ms）
2. 上传本地 `.mp3` / `.mp4`，文件会先上传七牛（若已配置），再流式同传
3. 故意说模糊发音，观察历史字幕 **「已纠错」** 标记
4. 勾选/取消 **中文语音播报** 测试 TTS
5. 上传带 **背景音乐的视频**，勾选「人声分离」，观察 BGM 状态与识别准确率
6. 点击 **导出 SRT** 下载字幕文件

## 项目结构

```
ai-real-time-interpreter/
├── manage.py
├── requirements.txt
├── README.md
├── CONTRIBUTING.md      # Commit / PR 规范
├── .env.example
├── core/
└── interpreter/
    ├── views.py
    ├── urls.py
    ├── utils/
    │   ├── asr.py           # Whisper 流式识别
    │   ├── translate.py     # 翻译 + 上下文纠错
    │   ├── tts.py           # Edge-TTS
    │   ├── stream.py        # 音频缓冲分片
    │   ├── pipeline.py      # 非阻塞处理管线
    │   ├── circuit_breaker.py
    │   └── qiniu_storage.py # 七牛 Kodo
    └── templates/index.html
```

## API

| 路径 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页面 |
| `/api/health/` | GET | 健康检查、延迟配置、熔断状态 |
| `/api/audio/chunk/` | POST | 音频分片 → ASR + 翻译 + 纠错 |
| `/api/upload/` | POST | 完整文件上传七牛云 |
| `/api/video/ingest/` | POST | 视频/音频预处理（BGM 检测、人声分离） |
| `/api/export/subtitles/` | GET | 导出 SRT/TXT 字幕 |
| `/api/session/reset/` | POST | 清空会话 |
| `/api/tts/` | POST | 中文 TTS |

## Git 提交规范

详见 [CONTRIBUTING.md](CONTRIBUTING.md)。示例：

```bash
git commit -m "feat(core): 项目基础骨架，实现麦克风实时收音+直译字幕展示"
git commit -m "feat(translate): 添加上下文缓存，实现历史字幕AI自动纠错修正"
git commit -m "feat(qiniu): 集成七牛SDK，音视频文件云端存储+访问"
```

## 仓库权限（赛事要求）

- **6.7 23:59 前**：保持私有
- **6.8 00:00 起**：改为公开
- **6.7 23:59 后**：停止一切 push

## 常见问题

1. **`[待翻译]` 占位** → 配置 `LLM_API_KEY`
2. **音频解码失败** → 安装 ffmpeg 并加入 PATH
3. **Whisper 慢** → `WHISPER_MODEL=tiny` 或 `WHISPER_DEVICE=cuda`
4. **七牛未显示** → 检查四项 QINIU_* 配置

## License

MIT
