# AI 同声传译助手

基于深度学习的实时语音同声传译系统：麦克风或本地音视频输入，Whisper 流式语音识别，Hy-MT2 本地 GPU 翻译，WebSocket 实时推送双语字幕。

## 功能

**输入与识别**
- 麦克风实时收音、本地视频/音频上传（MP4、MOV、AVI 等）
- 流式 ASR（faster-whisper，Whisper medium），GPU 加速识别
- 灵活分片模式：低延迟 200ms / 均衡 1000ms / 高准确率 3000ms
- 视频 BGM 检测与人声分离（Spleeter，仅视频/文件；麦克风路径自动跳过）
- VAD 语音活动检测 + 置信度过滤，减少幻觉输出

**翻译与展示**
- **Hy-MT2-1.8B 本地 GPU 翻译**（腾讯混元开源），带上下文历史，支持 33 种语言
- Google Translate 本地降级（Hy-MT2 不可用时自动切换）
- **口语压缩**：去除 um/you know 等填充词后再翻译
- **句子级断句**：检测完整句子边界（句号、问号、逗号）才触发翻译，避免碎片化
- **PPT / 演讲上下文**：注入翻译 prompt，提升术语一致性
- **人工修正**：点击字幕行手动改译文，或 LLM 结合上下文重译
- 术语表 / 热词、双语字幕、说话人 A/B 标注
- 字幕样式可调（字号、颜色、位置）

**实时推送**
- Django Channels WebSocket：`ws/interpreter/<session_id>/`
- 音频通过 WebSocket 传输 PCM 流，消除 HTTP 连接开销

**记录与导出**
- 同传历史本地 / 服务端保存
- 导出 SRT、VTT、TXT，或 ZIP 打包（含 JSON）
- 内置样例演示（快捷键 `D`），无麦克风可跑通

**运维**
- 状态页 `/status`：Whisper、翻译模型、Redis、WebSocket、熔断器
- Docker Compose：Redis + daphne + 健康检查
- GitHub Actions CI + `scripts/selftest.sh`

## 架构

```
浏览器
  ├─ WS    ws/interpreter/<session>/  音频 PCM 流 + 实时字幕推送
  ├─ HTTP  POST /api/audio/chunk/     音频分片（HTTP 回退）
  ├─ HTTP  POST /api/correct/         手动 / LLM 修正
  ├─ HTTP  POST /api/upload-ppt/      PPT / 演讲上下文
  └─ HTTP  POST /api/video/ingest/    视频上传 + BGM 分离

服务端流水线
  PCM → Whisper medium → 口语压缩 → 句子断句 → Hy-MT2 翻译 → WebSocket 推送
                                                               ↓
                                                   双语字幕实时显示

翻译引擎
  主：Hy-MT2-1.8B（腾讯混元，本地 GPU，带上下文历史）
  降级：Google Translate（deep-translator）

存储（可选 Redis）
  REDIS_URL 配置后：Channel Layer + 会话 pickle（history / 视频缓冲 / 术语表）
  未配置：InMemory Channel Layer + 进程内存会话
```

## 技术栈

Python 3.10 · Django 4.2 · Django Channels · daphne · faster-whisper · Transformers (Hy-MT2-1.8B) · PyTorch · deep-translator · edge-tts · Spleeter · Redis（可选）· pydub / ffmpeg

## 快速开始

```bash
git clone https://github.com/YANS311/ai-real-time-interpreter.git
cd ai-real-time-interpreter
python3.10 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\Activate.ps1
pip install -U pip -r requirements.txt
cp .env.example .env              # 配置环境变量
daphne -b 127.0.0.1 -p 8000 core.asgi:application
```

浏览器打开 <http://127.0.0.1:8000/>。WebSocket 需 daphne（或安装 daphne 后 `runserver`）。

**依赖：**
- 系统需安装 [ffmpeg](https://ffmpeg.org/) 并加入 PATH
- GPU 推理需 CUDA + PyTorch（Whisper medium 1.5GB + Hy-MT2-1.8B）
- 首次运行自动下载模型（可设置 `HF_HUB_OFFLINE=1` 使用缓存）

验证：

```bash
python manage.py check
chmod +x scripts/selftest.sh
./scripts/selftest.sh http://127.0.0.1:8000
```

## 环境变量

| 变量 | 说明 |
|------|------|
| `WHISPER_MODEL` | Whisper 模型：`tiny` / `base` / `small` / `medium`，推荐 `medium` |
| `WHISPER_DEVICE` | `cpu` 或 `cuda`（GPU 推理） |
| `WHISPER_COMPUTE_TYPE` | `int8` / `float16`，GPU 建议 `float16` |
| `WHISPER_MODEL_PATH` | 本地模型路径（可选，跳过下载） |
| `WHISPER_WARMUP` | 启动预加载 Whisper，默认 `True` |
| `AUDIO_CHUNK_DURATION_SEC` | 默认分片时长（秒），默认 `0.5` |
| `SPEECH_COMPRESSOR_ENABLED` | 口语压缩开关，默认 `True` |
| `HF_HUB_OFFLINE` | HuggingFace 离线模式，设为 `1` 使用缓存模型 |
| `TRANSFORMERS_OFFLINE` | Transformers 离线模式，设为 `1` |
| `PPT_CONTEXT_MAX_CHARS` | PPT 上下文最大字符 |
| `GLOSSARY` | 默认术语表，如 `GPT=生成式预训练模型` |
| `REDIS_URL` | Redis 地址（Compose 自动注入；未配则内存模式） |
| `SESSION_TTL` | 会话过期秒数，默认 `3600` |
| `DATA_UPLOAD_MAX_MB` | 视频/文件上传上限，默认 `200` |

GPU 推理配置示例：

```env
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
WHISPER_MODEL=medium
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

## Docker

```bash
cp .env.example .env   # 配置环境变量
docker compose up --build
```

Compose 启动 **Redis 7** + **interpreter**（daphne），自动注入 `REDIS_URL`。访问 <http://127.0.0.1:8000/>，`/status/` 查看模型和 WebSocket 状态。

> 注意：Docker 部署需确保 GPU 可用（nvidia-container-toolkit），否则翻译模型将降级到 Google Translate。

## 使用说明

1. **麦克风**：选源语言 → 选择分片模式（低延迟/均衡/高准确率）→ 开始收音
2. **视频**：上传 MP4 等 → 选择分片模式 → 开始同传；可勾选 BGM 分离和源音频播放
3. **术语 / PPT**：填写术语表或 PPT 上下文 → 保存，翻译时自动注入
4. **样例演示**：点「样例演示」或按 **`D`**
5. **修正字幕**：点击字幕行 → 保存修正 / LLM 重译
6. **导出**：SRT / VTT / ZIP；**监控**：[/status/](http://127.0.0.1:8000/status/)

### 分片模式

| 模式 | 分片时长 | 适用场景 |
|------|----------|----------|
| 低延迟 | 200ms | 直播、麦克风实时同传 |
| 均衡 | 1000ms | 麦克风日常使用 |
| 高准确率 | 3000ms | 视频/音频文件翻译 |

### 演示视频

通过网盘分享的文件：中传队_AI同声传译_demo演示.mp4
链接: https://pan.baidu.com/s/1cqyRzXotLuR6eo0OUkaW-g?pwd=qg2x 提取码: qg2x

### 答辩演示

1. 启动服务，首页确认 **WS 已连接**
2. 麦克风实时同传 → 展示延迟指标（目标 < 2s）
3. 上传视频文件 → 展示 BGM 分离 + 源音频播放
4. 导出 SRT 字幕
5. 打开 **`/status/`** 查看模型状态

快捷键：`D` 样例 · 视频 `空格` 暂停

## 近期优化

- **Hy-MT2 本地 GPU 翻译**：替换远程 LLM API，翻译延迟从 6-24s 降至 0.7-1.2s
- **低延迟分片**：支持 200ms/1000ms/3000ms 三种模式灵活切换
- **WebSocket 音频传输**：音频通过 WebSocket 发送，消除 HTTP 连接开销
- **ASR 质量优化**：收紧置信度过滤 + VAD 参数调优，减少幻觉输出
- **句子级断句**：完整句子边界检测，避免碎片化翻译
- **源音频播放**：视频处理时可同步播放原始音频

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
│       ├── asr.py           # faster-whisper ASR
│       ├── translate.py     # Hy-MT2 + Google 翻译
│       ├── pipeline.py      # ASR → 压缩 → 断句 → 翻译
│       ├── segmentation.py  # 句子边界检测
│       ├── speech_compressor.py
│       ├── correction.py / glossary.py / ppt_context.py
│       ├── stream.py        # 会话管理 + 音频处理
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
- **翻译显示 `[待翻译]`** — Hy-MT2 模型未加载成功，检查 GPU 和 transformers 版本。
- **Whisper 慢 / 首包慢** — 确认 `WHISPER_WARMUP=True`；GPU 推理需 CUDA + float16。
- **Whisper 幻觉输出** — 系统会自动过滤低置信度段；若仍有问题可调整 `no_speech_threshold`。
- **麦克风卡顿** — 选择「低延迟」模式（200ms 分片）；勿开 BGM 分离。
- **WS 未连接** — 使用 `daphne core.asgi:application` 启动。
- **Redis 未连接** — 本地可留空 `REDIS_URL`；Docker 用 `docker compose up`。
- **视频上传失败** — 检查文件小于 `DATA_UPLOAD_MAX_MB`（默认 200MB）。
- **HuggingFace 下载失败** — 设置 `HF_HUB_OFFLINE=1` 使用本地缓存模型。

## 开发

Conventional Commits，详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

```bash
./scripts/create-pr.sh    # 需 gh CLI
```

## License

MIT
