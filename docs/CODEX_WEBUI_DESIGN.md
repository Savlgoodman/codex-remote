# codex-webui 设计文档

日期：2026-06-02

工作目录：`C:\Users\pengy\project\codex-remote`

## 目标

做一个本机运行的 `codex-server` 和浏览器前端 `codex-webui`，用于远程观察和有限控制本机 Codex 工作状态。

第一版目标：

- 展示最近 / 全量线程，按活跃时间倒序排序。
- 点击线程后读取完整消息历史。
- 实时同步 Codex App / VSCode 插件中的 live 状态和流式输出。
- 在 IPC owner 可用时，通过 Web UI 向当前 UI 会话发送消息。
- 在 IPC 不可用时明确提示“当前只能读历史，不能控制 UI owner”。

非目标：

- 不直接把 `\\.\pipe\codex-ipc` 暴露到网络。
- 不把 IPC 当作全量线程数据库。
- 不在第一版实现复杂权限审批 UI；先只做普通文本发送和状态观察。
- 不强依赖 Codex App / VSCode 必须运行；读取线程和历史应尽量可在无 UI 时工作。

## 核心判断

当前通道分工如下：

```text
SDK / app-server / rollout:
  负责全量线程列表、完整历史、无 UI 后台读取。

codex-ipc:
  负责 App/VSCode live 状态、snapshot + patches、UI owner 控制。

codex-server:
  合并两边状态，给 Web UI 提供稳定 API。
```

因此，Web UI 上每个线程应有一个来源状态：

```text
live:
  当前 IPC 收到过 snapshot/patches，且 owner 仍在线。

stale:
  之前 IPC 收到过，但当前 IPC 断开或 owner 未确认在线。

history-only:
  只从 SDK / rollout / 本地历史读到，没有 live IPC 状态。
```

## 总体架构

```text
Browser codex-webui
  -> HTTP API
       GET /api/threads
       GET /api/threads/{conversationId}
       POST /api/threads/{conversationId}/messages
       POST /api/threads/{conversationId}/interrupt
  -> WebSocket 或 SSE
       /api/events
       /api/threads/{conversationId}/events

Python codex-server
  -> ThreadRepository
       SDKReader
       RolloutReader
  -> IpcClient
       connect / reconnect
       initialize
       snapshot + patches
       follower request
  -> StateStore
       merge full history + live state
       active time sort
       subscribers
  -> ControlService
       preflight
       build turnStartParams
       send via IPC owner
```

## 后端模块设计

### 1. `IpcClient`

职责：

- 连接 `\\.\pipe\codex-ipc`。
- 发送 `initialize`。
- 读写 4 字节 little-endian length-prefixed JSON frame。
- 自动响应 `client-discovery-request` 为 `canHandle: false`，避免 WebUI 自己变成 owner。
- 接收 `thread-stream-state-changed` broadcast。
- 支持断线检测和重连。
- 支持发送 `thread-follower-*` request，并等待 response。

关键状态：

```python
class IpcStatus:
    online: bool
    client_id: str | None
    connected_at: float | None
    last_error: str | None
    last_seen_at: float | None
```

关键方法：

```python
connect() -> None
disconnect() -> None
ensure_connected() -> bool
watch_forever() -> AsyncIterator[IpcEvent]
request(method: str, params: dict, version: int = 1, timeout: float = 45) -> dict
send_start_turn(conversation_id: str, turn_start_params: dict) -> dict
```

断线处理：

- Windows error `2 / 109 / 231 / 232 / 233` 视为 IPC 不可用或已断开。
- 后端不崩溃，设置 `ipc.online = false`。
- Web UI 收到 `ipc.status` 事件后禁用控制按钮。
- 后端按固定间隔重连，例如 2 秒。

### 2. `SdkReader`

职责：

- 使用 Python SDK 或 `codex app-server --listen stdio://` 读取线程列表和线程详情。
- 提供不依赖 App/VSCode UI 的历史读取能力。
- 返回规范化后的线程元数据和消息列表。

关键方法：

```python
list_threads(limit: int | None = None) -> list[ThreadSummary]
read_thread(conversation_id: str) -> ThreadDetail
```

需要确认的点：

- SDK 中 thread id 是否总是等同于 IPC `conversationId`。
- SDK 能否直接读取所有历史 turn。
- SDK 返回的消息结构与 IPC `conversationState.turns` 如何映射。

### 3. `RolloutReader`

职责：

- 作为 SDK 读取失败或分页不足时的补充来源。
- 读取 `.codex\sessions\...\rollout-*.jsonl`。
- 从 JSONL 中还原消息、命令、工具调用、时间戳。

使用策略：

- 第一版可以先实现只读解析，不做写入。
- 优先使用 SDKReader，RolloutReader 作为 fallback。
- 如果 IPC snapshot 中有 `rolloutPath`，优先读取该路径。

### 4. `StateStore`

职责：

- 保存线程索引。
- 合并 SDK/rollout 的完整历史和 IPC 的 live 状态。
- 维护活跃时间排序。
- 向 WebSocket/SSE 订阅者广播状态变化。

数据结构：

```python
class ThreadSummary:
    conversation_id: str
    title: str
    cwd: str
    source: Literal["live", "stale", "history-only"]
    runtime_status: str
    latest_turn_status: str
    latest_item_preview: str
    active_at: float | None
    updated_at: float
    has_live_owner: bool

class ThreadDetail:
    summary: ThreadSummary
    messages: list[Message]
    raw_turns: list[dict]
    pagination: dict | None

class Message:
    id: str
    role: Literal["user", "assistant", "system", "tool", "command", "reasoning"]
    text: str
    status: str | None
    created_at: float | None
    raw: dict
```

活跃时间计算优先级：

1. IPC patch/snapshot 到达时间。
2. 最新 turn 的 `turnStartedAtMs` / 完成时间。
3. SDK / rollout 中最新消息时间。
4. 文件修改时间。

合并规则：

- SDK/rollout 负责完整 `messages`。
- IPC snapshot/patches 负责 live `runtime_status`、最新 item、当前流式 assistant 输出。
- 同一个 `conversationId` 同时出现时，以 IPC live 字段覆盖 UI 状态字段，但不丢弃 SDK 完整历史。
- IPC 断线后，所有 live 线程降级为 `stale`，保留最后快照。

### 5. `ControlService`

职责：

- 处理 Web UI 的发送消息、打断、后续 steering。
- 每次控制前做 preflight。
- 构造 `turnStartParams`。
- 调用 `IpcClient.request("thread-follower-start-turn", ...)`。

发送消息 preflight：

```text
1. 检查 ipc.online。
2. 检查目标 conversationId 是否有最近 live snapshot。
3. 检查是否存在可处理 owner。
4. 检查当前 runtime/turn 状态是否允许 start turn。
5. 从最新 turn.params 或 currentPermissions 复制 cwd、sandboxPolicy、approvalPolicy、collaborationMode。
6. 给前端返回 send.accepted 或明确失败原因。
```

失败原因示例：

```text
ipc_offline:
  Codex App / VSCode 插件未运行，无法通过 UI owner 发送。

owner_not_found:
  IPC 在线，但没有 client 能 handle 该 conversation。

thread_busy:
  当前线程正在运行，普通 start-turn 暂不可用，可以考虑 steer/interrupt。

missing_snapshot:
  没有 live snapshot，无法安全构造 turnStartParams。
```

第一版发送策略：

- 默认只允许对 `live` 线程发送。
- 发送前后端再次检查 `sandboxPolicy`，如果是 `dangerFullAccess`，前端必须显示确认。
- 成功后由 IPC patches 驱动 UI 流式更新，不要只依赖 POST response。

## API 设计

### HTTP

`GET /api/status`

返回：

```json
{
  "ipc": {
    "online": true,
    "clientId": "...",
    "lastError": null
  },
  "sdk": {
    "available": true,
    "lastRefreshAt": 1780000000
  }
}
```

`GET /api/threads?limit=100`

返回按活跃时间倒序的线程：

```json
{
  "threads": [
    {
      "conversationId": "019e...",
      "title": "验证 Codex CLI 同步机制",
      "cwd": "C:\\Users\\pengy\\project\\codex-remote",
      "source": "live",
      "runtimeStatus": "idle",
      "latestTurnStatus": "completed",
      "latestItemPreview": "agent: ...",
      "activeAt": 1780000000,
      "hasLiveOwner": true
    }
  ]
}
```

`GET /api/threads/{conversationId}`

返回完整历史 + live summary：

```json
{
  "summary": { "...": "..." },
  "messages": [
    {
      "id": "turn-1-user",
      "role": "user",
      "text": "请验证同步机制",
      "status": "completed",
      "createdAt": 1780000000
    },
    {
      "id": "turn-1-agent-1",
      "role": "assistant",
      "text": "结论是...",
      "status": "completed",
      "createdAt": 1780000001
    }
  ]
}
```

`POST /api/threads/{conversationId}/messages`

请求：

```json
{
  "text": "继续验证一下同步状态",
  "confirmDangerFullAccess": false
}
```

返回：

```json
{
  "ok": true,
  "requestId": "...",
  "mode": "ipc-owner"
}
```

失败：

```json
{
  "ok": false,
  "error": "ipc_offline",
  "message": "Codex App / VSCode 插件未运行，无法通过 UI owner 发送。"
}
```

### 实时事件

第一版建议用 WebSocket：

```text
GET /api/events
```

原因：

- 后续可能需要双向交互，例如前端订阅指定线程、发送 ping、切换过滤条件。
- 发送消息本身仍走 HTTP POST，避免 WebSocket command 和事件混在一起。

事件类型：

```json
{ "type": "ipc.status", "online": true, "clientId": "..." }
{ "type": "threads.changed", "threads": [ "...summary..." ] }
{ "type": "thread.snapshot", "conversationId": "...", "summary": "...", "messages": [] }
{ "type": "thread.patch", "conversationId": "...", "patches": [] }
{ "type": "thread.message.delta", "conversationId": "...", "messageId": "...", "textDelta": "..." }
{ "type": "thread.message.completed", "conversationId": "...", "messageId": "..." }
{ "type": "control.result", "requestId": "...", "ok": true }
```

SSE 也可行，但第一版如果用 FastAPI，WebSocket 更自然；若只做单向流，SSE 更简单。

## 前端设计

项目名：`codex-webui`

建议技术栈：

- React + Vite + TypeScript。
- WebSocket client 订阅后端事件。
- Markdown 渲染：`react-markdown` + `remark-gfm`。
- 代码块高亮：`shiki` 或 `highlight.js`。
- diff/patch/command 块第一版先用简洁卡片。

布局：

```text
┌──────────────────────────────────────────────┐
│ Top Bar: IPC 状态 / SDK 状态 / 刷新 / 设置       │
├───────────────┬──────────────────────────────┤
│ Thread List   │ Thread Detail                 │
│               │                              │
│ cwd group     │ message stream                │
│ thread item   │ command/tool cards            │
│ thread item   │ composer                      │
└───────────────┴──────────────────────────────┘
```

线程列表：

- 默认按 `activeAt desc`。
- 支持 cwd 分组。
- 每个 item 显示 title、cwd、runtime、latest item preview、live/stale/history-only 标记。
- live 线程在 IPC patch 到达时自动上浮。

线程详情：

- 点击线程后先调用 `GET /api/threads/{conversationId}` 获取完整历史。
- 如果该线程 live，再叠加 WebSocket patch/delta。
- 消息渲染优先按规范化 `Message`，保留 raw 查看按钮。
- assistant 流式输出用同一个 message bubble 追加 delta。
- command/tool 输出用可折叠区域展示。

发送框：

- 只在 `summary.hasLiveOwner = true` 且 `ipc.online = true` 时启用。
- 如果线程 busy，主按钮变为不可用，并显示可选 `Interrupt`。
- 发送后清空输入，等待 WebSocket 更新。
- 如果后端返回 `dangerFullAccess_requires_confirmation`，弹确认再重发。

参考 VSCode 插件渲染：

- 可以参考它对 `thread-stream-state-changed` 中 `turns/items` 的 item 类型分类。
- 第一版不需要完整复刻 VSCode UI；重点是把 `userMessage`、`agentMessage`、`commandExecution`、`reasoning`、`mcpToolCall` 几类先渲染清楚。
- 后续再补命令输出、审批、附件、图片、文件引用等复杂 item。

## 数据流

### 启动

```text
1. codex-server 启动。
2. 初始化 StateStore。
3. SdkReader 刷新线程列表。
4. IpcClient 尝试连接 codex-ipc。
5. 如果 IPC 在线，接收 snapshots，把线程标记为 live。
6. 如果 IPC 离线，Web UI 仍可展示 SDK/rollout 线程，但控制不可用。
```

### 打开线程

```text
1. 前端点击线程。
2. GET /api/threads/{conversationId}。
3. 后端优先从 SDKReader 读取完整历史。
4. 如 SDK 不足或失败，尝试 RolloutReader。
5. 合并 StateStore 中 live snapshot。
6. 返回 messages。
7. 前端继续听 WebSocket 的 thread.patch / delta。
```

### 发送消息

```text
1. 前端 POST /api/threads/{conversationId}/messages。
2. ControlService 执行 preflight。
3. IPC 在线且 owner 可用时，构造 thread-follower-start-turn。
4. IpcClient 发送 request。
5. App/VSCode owner 真正调用自己的 app-server 开始 turn。
6. 后续输出通过 IPC patches 回到 codex-server。
7. codex-server 转成 WebSocket 事件推给前端。
```

### IPC 断线 / 重连

```text
1. IpcClient 捕获 pipe 断线。
2. StateStore 把 live 线程降级为 stale。
3. WebSocket 广播 ipc.status online=false。
4. 前端禁用发送按钮。
5. IpcClient 后台重连。
6. 重连成功后等待 App/VSCode owner 广播 snapshot。
7. 收到 snapshot 后对应线程恢复 live。
```

## 安全设计

本机开发阶段：

- `codex-server` 默认只监听 `127.0.0.1`。
- 控制接口默认关闭，需要环境变量 `CODEX_WEBUI_ENABLE_CONTROL=1`。
- 第一次版本可不做登录，但要打印明显提示。

远程访问阶段：

- 必须加 token，例如 `CODEX_WEBUI_TOKEN`。
- 所有 POST 控制接口要求 token。
- WebSocket 连接要求 token。
- 审计日志记录：时间、conversationId、操作、cwd、sandboxPolicy、结果。
- 不把 raw payload 默认传给远端；raw 只在本机 debug 模式可见。

危险权限处理：

- 如果 `sandboxPolicy.type = dangerFullAccess`，前端发送必须二次确认。
- 如果 `approvalPolicy = never` 且 `dangerFullAccess`，前端显示高风险提示。
- 第一版不自动批准命令/file approval。

## 推荐目录结构

```text
codex-remote/
  docs/
    CODEX_CHANNEL_RESEARCH.md
    CODEX_WEBUI_DESIGN.md
  server/
    codex_server/
      __init__.py
      main.py
      ipc_client.py
      sdk_reader.py
      rollout_reader.py
      state_store.py
      control_service.py
      models.py
      normalizer.py
    requirements.txt
  webui/
    package.json
    index.html
    src/
      main.tsx
      api.ts
      websocket.ts
      App.tsx
      components/
        ThreadList.tsx
        ThreadDetail.tsx
        MessageView.tsx
        Composer.tsx
        StatusBar.tsx
```

## MVP 实现顺序

1. 后端 `models.py`：定义 `ThreadSummary`、`ThreadDetail`、`Message`、`IpcStatus`。
2. 后端 `ipc_client.py`：把现有 `codex_ipc_thread_watch.py` 的 frame、connect、initialize、patch 应用逻辑抽成类。
3. 后端 `state_store.py`：保存 IPC snapshot，提供线程排序。
4. 后端 `main.py`：FastAPI，先实现 `GET /api/status`、`GET /api/threads`、WebSocket `/api/events`。
5. 后端 `sdk_reader.py`：接入 SDK 列线程和读完整历史。
6. 前端 `codex-webui`：线程列表 + 详情页 + WebSocket live 更新。
7. 后端 `control_service.py`：实现 `POST /messages`，只允许 live owner 线程发送。
8. 前端 Composer：支持发送、错误提示、IPC 离线禁用。
9. 增加 `RolloutReader` fallback。
10. 增加审计日志和 token。

## 待确认问题

- Python SDK 的 `thread_list` 是否能稳定返回所有线程，分页字段是什么。
- Python SDK 的 `thread_resume/read` 与 IPC `conversationId` 是否一一对应。
- App/VSCode owner discovery 的失败 response 具体形状，需要在控制请求失败时记录。
- `thread-follower-start-turn` 在 busy thread 上是否应该禁用，还是转用 `thread-follower-steer-turn`。
- VSCode 插件中 command/tool/message item 的完整类型表需要继续整理。
- 长历史会话分页时，SDK 与 rollout 哪个更可靠。

## 当前建议

第一版不要试图“完全替代 Codex App”。更稳的路线是：

```text
codex-webui = 历史浏览器 + live 观察器 + IPC owner 控制面板
```

这样既能利用 SDK/app-server 做完整历史，又能利用 IPC 保持和 App/VSCode 的同步。等这条链路稳定后，再逐步做后台运行、审批、附件、命令输出、文件引用和更完整的 VSCode 风格渲染。
