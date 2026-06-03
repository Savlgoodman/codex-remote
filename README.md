# codex-remote

本项目当前包含一个 MVP 版 `codex-server` + `codex-webui`，用于观察和有限控制本机 Codex App / VSCode 插件中的 live 会话。

## 启动

后端：

```powershell
cd C:\Users\pengy\project\codex-remote\server
python -m uvicorn codex_server.main:app --host 127.0.0.1 --port 8765
```

前端：

```powershell
cd C:\Users\pengy\project\codex-remote\webui
npm install
npm run dev -- --port 5173
```

打开：

```text
http://127.0.0.1:5173
```

## 当前能力

- `GET /api/status`：查看 SDK、IPC、控制状态。
- `GET /api/threads`：按活跃时间倒序列出线程，合并 SDK 历史和 IPC live 状态。
- `GET /api/threads/{conversationId}`：读取完整消息历史，优先走 Python SDK。
- `POST /api/threads/{conversationId}/messages`：统一发送入口；后端根据 IPC 状态与线程 live owner 自动路由到 IPC owner 或 SDK resume。
- `WS /api/events`：推送 IPC 状态、线程列表变化和线程 snapshot/patch。

## 控制开关

后端默认只绑定 `127.0.0.1`，MVP 默认开启控制。要关闭发送能力：

```powershell
$env:CODEX_WEBUI_ENABLE_CONTROL="0"
python -m uvicorn codex_server.main:app --host 127.0.0.1 --port 8765
```

## 重要限制

- IPC 只在 Codex App 或 VSCode Codex 插件运行时可用。
- App/VSCode 都关闭时，WebUI 仍可读 SDK 历史，但不能通过 IPC 控制 UI 会话。
- 当前消息渲染是 MVP 版，先覆盖 user/agent/command/tool/reasoning。
- `RolloutReader` 还是占位，后续再补 `.codex\sessions\...\rollout-*.jsonl` fallback。
- 远程开放前必须增加 token 和审计日志，不要把服务直接暴露到公网。
