# AI 同声传译助手

基于 Django 的实时同声传译 Web 应用：麦克风或本地音视频输入，流式 Whisper 识别，口语压缩与大模型翻译，WebSocket 实时推送字幕，支持人工修正与 Redis 多 worker 部署。

## 功能

**输入与识别**
- 麦克风实时收音、本地视频/音频上传（MP4、MOV、AVI 等）
- 流式 ASR（faster-whisper），低延迟 300ms / 高准确率 800ms 分片（前后端同步）
- 视频 BGM 检测与人声分离（Spleeter，仅视频/文件；麦克风路径自动跳过）
- 启动时 Whisper 模型预热（`WHISPER_WARMUP`）

**翻译与展示**
- 大模型实时中文字幕，上下文自动纠错
- **口语压缩**：去除 um/you know 等填充词后再翻译
- **PPT / 演讲上下文**：注入翻译 prompt，提升术语一致性
- **人工修正**：点击字幕行手动改译文，或 LLM 结合上下文重译
- 术语表 / 热词、双语字幕、演讲模式、Edge-TTS、说话人 A/B 标注
- 字幕样式可调（字号、颜色、位置）

**实时推送**
- Django Channels WebSocket：`ws/interpreter/<session_id>/`
- 音频分片走 HTTP，字幕与修正事件走 WebSocket（可回退 HTTP）

**记录与导出**
- 同传历史本地 / 服务端保存
- 导出 SRT、VTT、TXT，或 ZIP 打包（含 JSON）
- 内置样例演示（快捷键 `D`），无麦克风可跑通

**运维**
- 状态页 `/status`：Whisper、LLM、Redis、WebSocket、熔断器
- Docker Compose：Redis + daphne + 健康检查
- GitHub Actions CI + `scripts/selftest.sh`

## 架构

```
浏览器
  ├─ HTTP  POST /api/audio/chunk/     音频分片 → ASR → 口语压缩 → 翻译
  ├─ HTTP  POST /api/correct/         手动 / LLM 修正
  ├─ HTTP  POST /api/upload-ppt/      PPT / 演讲上下文
  └─ WS    ws/interpreter/<session>/  实时字幕 / 修正推送

服务端流水线
  PCM → Whisper → speech_compressor → translate(+glossary,+ppt) → history
                                                          ↓
                                              Channels → WebSocket

存储（可选 Redis）
  REDIS_URL 配置后：Channel Layer + 会话 pickle（history / 视频缓冲 / 术语表）
  未配置：InMemory Channel Layer + 进程内存会话
```

## 技术栈

Python 3.10 · Django 4.2 · Django Channels · daphne · faster-whisper · httpx（OpenAI 兼容 API）· edge-tts · Redis（可选）· pydub / ffmpeg · 七牛 Kodo（可选）

## 快速开始

```bash
git clone https://github.com/YANS311/ai-real-time-interpreter.git
cd ai-real-time-interpreter
python3.10 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\Activate.ps1
pip install -U pip -r requirements.txt
cp .env.example .env              # 填写 LLM_API_KEY
daphne -b 127.0.0.1 -p 8000 core.asgi:application
```

浏览器打开 <http://127.0.0.1:8000/>。WebSocket 需 daphne（或安装 daphne 后 `runserver`）。

**依赖：** 系统需安装 [ffmpeg](https://ffmpeg.org/) 并加入 PATH。首次运行会下载 Whisper 模型（`base` 约 150MB）。

验证：

```bash
python manage.py check
chmod +x scripts/selftest.sh
./scripts/selftest.sh http://127.0.0.1:8000
```

`llm_configured` 为 `false` 时翻译显示 `[待翻译]`，请在 `.env` 配置 `LLM_API_KEY`。

## 环境变量

| 变量 | 说明 |
|------|------|
| `LLM_API_KEY` | 翻译、口语压缩、修正（必填） |
| `LLM_API_BASE` / `LLM_MODEL` | API 地址与模型 |
| `WHISPER_MODEL` | `tiny` / `base` / `small`，答辩机建议 `tiny` |
| `WHISPER_WARMUP` | 启动预加载 Whisper，默认 `True` |
| `WHISPER_DEVICE` | `cpu` 或 `cuda` |
| `AUDIO_CHUNK_DURATION_SEC` | 默认分片 0.3s |
| `SPEECH_COMPRESSOR_ENABLED` | 口语压缩开关，默认 `True` |
| `PPT_CONTEXT_MAX_CHARS` | PPT 上下文最大字符 |
| `GLOSSARY` | 默认术语表，如 `GPT=生成式预训练模型` |
| `REDIS_URL` | Redis 地址（Compose 自动注入；未配则内存模式） |
| `REDIS_SESSIONS_ENABLED` | Redis 会话存储，默认 `True` |
| `CHANNELS_ENABLED` | WebSocket 开关，默认 `True` |
| `SESSION_TTL` | 会话过期秒数，默认 `3600` |
| `QINIU_*` | 七牛云存储（可选） |

GPU 示例：

```env
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
```

## Docker

```bash
cp .env.example .env   # 填写 LLM_API_KEY
docker compose up --build
```

Compose 启动 **Redis 7** + **interpreter**（daphne），自动注入 `REDIS_URL`。访问 <http://127.0.0.1:8000/>，`/status/` 查看 Redis / WebSocket 状态。

## 使用说明

1. **麦克风**：选源语言后开始收音，勿勾选 BGM（仅视频/文件适用）。
2. **视频**：上传 MP4 等 → 开始同传；可勾选 BGM 分离；进度条 seek 会同步截断字幕。
3. **术语 / PPT**：填写术语表或 PPT 上下文 → 保存，翻译时自动注入。
4. **样例演示**：点「样例演示」或按 **`D`**。
5. **修正字幕**：点击字幕行 → 保存修正 / LLM 重译。
6. **导出**：SRT / VTT / ZIP；**监控**：[/status/](http://127.0.0.1:8000/status/)。

### 答辩演示（约 3 分钟）

1. `./scripts/selftest.sh http://127.0.0.1:8000`
2. 首页确认 **LLM 就绪**、**WS 已连接**
3. 按 **`D`** → 导出 SRT + ZIP
4. 点击字幕演示 **手动修正 / LLM 重译**
5. 打开 **`/status/`** 讲 Whisper + Redis + Channels
6. （可选）短麦克风同传，BGM / TTS 关闭

快捷键：`D` 样例 · `P` 演讲模式 · 视频 `空格` 暂停 · `Esc` 退出演讲

截图放 `docs/screenshots/`（如 `main.png`、`status.png`）。

## 目录结构

```
ai-real-time-interpreter/
├── core/                    # Django / ASGI / Channels 配置
├── interpreter/
│   ├── consumers.py         # WebSocket 消费者
│   ├── routing.py           # WS 路由
│   ├── views.py             # HTTP API
│   ├── templates/           # 主页 + /status
│   └── utils/
│       ├── asr.py           # faster-whisper
│       ├── speech_compressor.py
│       ├── translate.py / correction.py
│       ├── ppt_context.py / glossary.py
│       ├── pipeline.py      # ASR → 压缩 → 翻译
│       ├── redis_client.py / session_store.py
│       └── ws_events.py
├── docs/screenshots/
├── scripts/selftest.sh
├── Dockerfile
└── docker-compose.yml       # redis + interpreter
```

## API

| 路径 | 方法 | 说明 |
|------|------|------|
| `/api/health/` | GET | 健康检查 |
| `/api/status/` | GET | 运行状态（含 Redis） |
| `/api/audio/chunk/` | POST | 音频分片（ASR + 翻译） |
| `/api/video/ingest/` | POST | 视频预处理 + BGM 分离 |
| `/api/upload-ppt/` | POST | PPT / 演讲上下文 |
| `/api/upload-terms/` | POST | 术语表 JSON |
| `/api/correct/` | POST | 手动 / LLM 修正 |
| `/api/demo/script/` | GET | 样例字幕 |
| `/api/session/control/` | POST | 暂停 / 继续 / seek |
| `/api/export/subtitles/` | GET/POST | SRT / VTT / TXT |
| `/api/export/bundle/` | GET/POST | ZIP 导出 |
| `/api/history/` | GET | 历史列表 |
| `/api/tts/` | POST | Edge-TTS |
| `/ws/interpreter/<session_id>/` | WS | 字幕推送；`{"action":"correct"}` |

完整路由见 `interpreter/urls.py`。

## 常见问题

- **音频解码失败** — 确认 ffmpeg 在 PATH 中。
- **`[待翻译]`** — 配置 `LLM_API_KEY` 后重启。
- **Whisper 慢 / 首包慢** — 用 `WHISPER_MODEL=tiny`；确认 `WHISPER_WARMUP=True`。
- **麦克风卡顿** — 勿开 BGM 分离；可关口语压缩与 TTS 降延迟。
- **WS 未连接** — 使用 `daphne core.asgi:application` 启动。
- **Redis 未连接** — 本地可留空 `REDIS_URL`；Docker 用 `docker compose up`。

## 开发

Conventional Commits，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

```bash
./scripts/create-pr.sh    # 需 gh CLI
```

## License

MIT
