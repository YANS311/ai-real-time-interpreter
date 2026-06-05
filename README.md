# AI 同声传译助手

基于 Django 的实时同声传译 Web 应用：麦克风或本地音视频输入，流式 Whisper 识别，大模型翻译并带上下文纠错，结果以滚动字幕展示，可导出 SRT / VTT 等格式。

## 功能

**输入与识别**
- 麦克风实时收音、本地视频/音频上传（MP4、MOV、AVI 等）
- 流式 ASR，默认 300ms 分片；可切换高准确率模式
- 视频 BGM 检测与人声分离（可选 Spleeter），环境降噪

**翻译与展示**
- 大模型实时中文字幕，支持纠错历史行
- 双语字幕、演讲模式（全屏大字幕）、Edge-TTS 播报
- 术语表/热词、轻量说话人 A/B 标注
- 字幕样式可调（字号、颜色、位置）

**记录与导出**
- 同传历史本地/服务端保存
- 导出 SRT、VTT、TXT，或 ZIP 打包（含 JSON）
- 内置样例演示脚本，无麦克风也能跑通流程

**其他**
- 视频同传暂停、进度条与跳转
- 识别质量与延迟指标
- 状态页 `/status`
- Docker Compose 部署

## 技术栈

Python 3.10、Django 4.2、faster-whisper、httpx（OpenAI 兼容 API）、edge-tts、pydub/ffmpeg。七牛 Kodo 对象存储为可选配置。

## 快速开始

```bash
git clone https://github.com/YANS311/ai-real-time-interpreter.git
cd ai-real-time-interpreter
python3.10 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\Activate.ps1
pip install -U pip -r requirements.txt
cp .env.example .env                # 填写 LLM_API_KEY
python manage.py runserver
```

浏览器打开 <http://127.0.0.1:8000/>。

**依赖：** 系统需安装 [ffmpeg](https://ffmpeg.org/) 并加入 PATH。首次运行会下载 Whisper 模型（`base` 约 150MB）。

验证：

```bash
python manage.py check
curl http://127.0.0.1:8000/api/health/
```

`llm_configured` 为 `false` 时翻译会显示占位文案，请在 `.env` 中配置 `LLM_API_KEY`。

## 环境变量

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` | 翻译与纠错（必填） |
| `LLM_API_BASE` / `LLM_MODEL` | API 地址与模型 |
| `WHISPER_MODEL` | `tiny` / `base` / `small`，低配可用 `tiny` |
| `WHISPER_DEVICE` | `cpu` 或 `cuda` |
| `AUDIO_CHUNK_DURATION_SEC` | 分片时长，默认 `0.3` |
| `GLOSSARY` | 默认术语表，如 `GPT=生成式预训练模型` |
| `QINIU_*` | 七牛云存储（可选） |

GPU 加速示例：

```env
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

镜像内已包含 ffmpeg。

## 平台说明

**macOS：** `brew install ffmpeg python@3.10`。麦克风需在系统隐私设置中授权浏览器。

**Linux：** `apt install ffmpeg python3.10-venv build-essential`。远程访问麦克风建议 HTTPS 或 localhost。

**Windows：** 从 python.org 安装 Python 并勾选 PATH；ffmpeg 可用 Chocolatey 或手动解压到 PATH。文件同传建议 Chrome/Edge。

可选人声分离：`pip install -r requirements-spleeter.txt`（体积较大，失败时可跳过，会自动降级）。

## 使用说明

1. 麦克风：选源语言后开始收音，字幕面板实时更新。
2. 视频：上传文件后开始同传；可勾选 BGM 分离，用暂停/进度条控制处理节奏。
3. 样例演示：点「样例演示」播放内置双语脚本；「演讲模式」适合投屏。
4. 导出：SRT/VTT 单文件，或 ZIP 含完整时间轴数据。
5. 监控：访问 [/status/](http://127.0.0.1:8000/status/) 查看服务状态。

快捷键：`D` 样例演示，`P` 演讲模式，视频同传中 `空格` 暂停/继续，`Esc` 退出演讲模式。点击字幕行可复制内容。

<!-- 截图可放在 docs/screenshots/ 并在下方引用 -->
<!-- ![主界面](docs/screenshots/main.png) -->

```bash
chmod +x scripts/selftest.sh
./scripts/selftest.sh http://127.0.0.1:8000
```

## 目录结构

```
ai-real-time-interpreter/
├── core/                 # Django 配置
├── interpreter/
│   ├── views.py          # HTTP 接口
│   ├── templates/        # 页面
│   ├── static/demo/      # 样例字幕
│   └── utils/            # ASR、翻译、BGM、导出等
├── scripts/
├── Dockerfile
└── docker-compose.yml
```

## API

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/health/` | GET | 健康检查 |
| `/api/status/` | GET | 运行状态 |
| `/api/audio/chunk/` | POST | 音频分片 |
| `/api/video/ingest/` | POST | 视频预处理 |
| `/api/demo/script/` | GET | 样例字幕 |
| `/api/session/control/` | POST | 暂停/继续/跳转 |
| `/api/export/subtitles/` | GET/POST | SRT/VTT/TXT |
| `/api/export/bundle/` | GET/POST | ZIP 导出 |
| `/api/history/` | GET | 历史列表 |
| `/api/tts/` | POST | 语音合成 |

完整路由见 `interpreter/urls.py`。

## 常见问题

- **音频解码失败** — 确认 ffmpeg 在 PATH 中。
- **`[待翻译]`** — 配置 `LLM_API_KEY` 后重启服务。
- **Whisper 慢** — 改用 `WHISPER_MODEL=tiny` 或启用 CUDA。
- **TTS 无声音** — 检查网络与系统音量。

## 开发

提交信息遵循 Conventional Commits，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

```bash
./scripts/create-pr.sh    # 需安装 gh CLI
```

## License

MIT
