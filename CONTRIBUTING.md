# 贡献说明

## Commit 规范

采用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
type(模块): 简短描述
```

常用 type：`feat`、`fix`、`perf`、`docs`、`refactor`、`chore`。

示例：

```bash
git commit -m "feat(asr): 接入流式 Whisper 识别"
```

## 分支

在 `feature/*` 或 `fix/*` 分支开发，通过 PR 合并到 `main`。

## Pull Request

PR 标题与 commit 风格一致。描述中建议包含：

- 改动摘要
- 如何自测（`python manage.py check`、`./scripts/selftest.sh`）

可用脚本辅助创建 PR：

```bash
chmod +x scripts/create-pr.sh
./scripts/create-pr.sh
```

需要安装 [GitHub CLI](https://cli.github.com/)。
