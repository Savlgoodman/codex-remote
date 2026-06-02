# Codex App / VSCode / SDK 会话通道调查记录

日期：2026-06-02

工作目录：`C:\Users\pengy\project\codex-remote`

## 结论摘要

这次调查确认了这些关键事实：

1. Python SDK 确实是通过本地 `codex app-server` runtime 工作，但它会启动自己的 `stdio` app-server 子进程，不等同于当前 Codex App / VSCode 插件 UI 会话通道。
2. VSCode 插件也会启动自己的 `codex app-server` 子进程，并通过 VSCode webview bridge 转发 `mcp-request`、`mcp-notification`、`mcp-response`。
3. Codex App 与 VSCode 能同步实时 UI 会话状态，关键在另一个本地 IPC 通道：Windows named pipe `\\.\pipe\codex-ipc`。外部进程可以连接这个 IPC，注册 client 后收到当前线程实时状态广播。
4. Codex CLI / Python SDK 自己运行 turn 时，不会实时同步到已经打开的 Codex App / VSCode IPC UI 状态；但 App/VSCode 关闭后重新打开，可能会通过自己的 server / 本地会话源重新加载到最新历史。
5. `codex-ipc` 不是常驻后台服务。App 和 VSCode 插件都关闭后，named pipe 会消失或重置；已有 watcher 会收到类似 Windows `ERROR_BROKEN_PIPE` / `PeekNamedPipe failed` 的断线错误。
6. 通过 IPC 发送 `thread-follower-start-turn` 已实测可以让 App/VSCode 同步并真正开始 UI owner 上的一轮对话；但前提是当前存在能处理该 conversation 的 App/VSCode owner。

因此：

- 如果目标是“用 SDK/脚本单独创建和运行 Codex 线程”，可以走 Python SDK 或直接 `codex app-server --listen stdio://`。
- 如果目标是“远程观察当前 Codex App / VSCode 正在工作的线程状态”，应优先接入 `codex-ipc`。
- 如果目标是“远程控制当前 UI 会话”，下一步应实现 `codex-ipc` 上的 `thread-follower-*` request 协议，而不是只调用 SDK。
- 如果目标是“列出全部线程 / 读取严格完整历史”，不能只依赖 IPC；应接入 SDK / app-server，必要时补充读取本机 rollout jsonl。

## 本机运行时与版本

### Python SDK

Python SDK 包路径：

`C:\Users\pengy\miniconda3\Lib\site-packages\openai_codex`

关键源码：

`C:\Users\pengy\miniconda3\Lib\site-packages\openai_codex\client.py`

重要发现：

- `RUNTIME_PKG_NAME = "openai-codex-cli-bin"`
- SDK 客户端说明为：`Synchronous typed JSON-RPC client for codex app-server over stdio`
- 默认启动参数为：

```text
codex.exe app-server --listen stdio://
```

SDK 捆绑 runtime：

`C:\Users\pengy\miniconda3\Lib\site-packages\codex_cli_bin\bin\codex.exe`

版本：

```text
codex-cli 0.132.0
```

### PATH 上的 Codex CLI

PATH 上的 `codex`：

`C:\nvm4w\nodejs\codex.ps1`

它转发到 npm 包：

`C:\nvm4w\nodejs\node_modules\@openai\codex\bin\codex.js`

版本：

```text
codex-cli 0.133.0
```

### VSCode 插件

已安装扩展：

`C:\Users\pengy\.vscode\extensions\openai.chatgpt-26.5527.60818-win32-x64`

插件主文件：

`C:\Users\pengy\.vscode\extensions\openai.chatgpt-26.5527.60818-win32-x64\out\extension.js`

插件捆绑 runtime：

`C:\Users\pengy\.vscode\extensions\openai.chatgpt-26.5527.60818-win32-x64\bin\windows-x86_64\codex.exe`

版本：

```text
codex-cli 0.136.0-alpha.2
```

插件 package 中显示扩展 ID：

```json
{
  "publisher": "openai",
  "name": "chatgpt",
  "displayName": "Codex - OpenAI's coding agent"
}
```

### Codex Desktop App

本机安装的 Desktop App：

```text
PackageFullName: OpenAI.Codex_26.527.3686.0_x64__2p2nqsd0c76g0
Version:         26.527.3686.0
InstallLocation: C:\Program Files\WindowsApps\OpenAI.Codex_26.527.3686.0_x64__2p2nqsd0c76g0
```

Desktop App 主进程：

```text
C:\Program Files\WindowsApps\OpenAI.Codex_26.527.3686.0_x64__2p2nqsd0c76g0\app\Codex.exe
```

Desktop App 捆绑 runtime 进程：

```text
C:\Program Files\WindowsApps\OpenAI.Codex_26.527.3686.0_x64__2p2nqsd0c76g0\app\resources\codex.exe app-server --analytics-default-enabled
```

Codex Desktop 还会在本地缓存另一份 runtime：

```text
C:\Users\pengy\AppData\Local\OpenAI\Codex\bin\7dea4a003bc76627\codex.exe
```

版本：

```text
codex-cli 0.135.0-alpha.1
```

当前进程中实际看到多类 runtime 并存：

```text
Desktop App runtime:
  C:\Program Files\WindowsApps\OpenAI.Codex_26.527.3686.0_x64__2p2nqsd0c76g0\app\resources\codex.exe app-server --analytics-default-enabled

Desktop/local runtime:
  C:\Users\pengy\AppData\Local\OpenAI\Codex\bin\7dea4a003bc76627\codex.exe app-server --listen stdio://

VSCode runtime:
  C:\Users\pengy\.vscode\extensions\openai.chatgpt-26.5527.60818-win32-x64\bin\windows-x86_64\codex.exe app-server --analytics-default-enabled

npm CLI runtime:
  C:\Users\pengy\AppData\Local\nvm\v22.22.2\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe app-server --listen stdio://
```

这进一步说明：App、VSCode、CLI/SDK 都可能启动自己的 `app-server`，但 App/VSCode 的“互相同步”并不是因为共用同一个 `stdio` app-server，而是通过额外的本地 IPC 协同层完成。

## Python SDK 通道

当前脚本：

`C:\Users\pengy\project\codex-remote\codex_threads_cli.py`

脚本逻辑：

- `with Codex(config=config) as codex`
- `codex.thread_list(...)`
- `codex.thread_resume(thread_id)`
- `thread.turn(text, sandbox=Sandbox.workspace_write)`
- `turn.handle.stream()`

SDK 的实际运行方式：

```text
Python process
  -> openai_codex.CodexClient
  -> bundled codex.exe
  -> codex app-server --listen stdio://
  -> JSON-RPC over stdin/stdout
```

这说明 SDK 能列出、读取、启动线程，但它默认是新起一个 app-server runtime，而不是复用当前 Codex App / VSCode UI 所在的实时会话桥。

## VSCode 插件通道

VSCode 插件启动时会创建类似 `CodexMcpConnection` 的对象。

关键行为：

```text
Spawning codex app-server
```

启动参数：

```text
codex.exe app-server --analytics-default-enabled
```

进程中实际观察到：

```text
c:\Users\pengy\.vscode\extensions\openai.chatgpt-26.5527.31454-win32-x64\bin\windows-x86_64\codex.exe app-server --analytics-default-enabled
c:\Users\pengy\.vscode\extensions\openai.chatgpt-26.5527.60818-win32-x64\bin\windows-x86_64\codex.exe app-server --analytics-default-enabled
```

插件 webview 到 app-server 的路径：

```text
VSCode webview
  -> postMessage
  -> extension host handleMessage()
  -> case "mcp-request"
  -> codexMcpConnection.sendRequest(...)
  -> app-server stdin/stdout JSON-RPC
```

插件从 app-server 收到结果后：

```text
codexMcpConnection.registerProvider(...)
  onResult       -> mcp-response
  onRequest      -> mcp-request
  onNotification -> mcp-notification
  onFatalError   -> codex-app-server-fatal-error
```

webview 使用的 message 类型包括：

- `mcp-request`
- `mcp-response`
- `mcp-notification`
- `codex-app-server-fatal-error`
- `thread-stream-state-changed`
- `thread-follower-start-turn-request`
- `thread-follower-steer-turn-request`
- `thread-follower-interrupt-turn-request`
- `thread-role-request`

## Codex IPC 通道

最重要发现：

Windows 上存在本地 named pipe：

```text
\\.\pipe\codex-ipc
```

插件源码中对应逻辑：

```js
function Ga() {
  if (process.platform === "win32") return path.join("\\\\.\\pipe", "codex-ipc");
  ...
}
```

该 IPC 用于多个 Codex client 之间的 owner/follower 协调。

协议格式：

- 4 字节 little-endian 长度前缀
- 后跟 UTF-8 JSON payload

### IPC 生命周期与可用性

新的实测现象：

- Codex App 和 VSCode 插件任意一个运行时，通常会存在 `\\.\pipe\codex-ipc`。
- Codex App 与 VSCode 插件同时关闭后，`codex-ipc` 会消失或重置。
- 已经连接的 watcher 会断开，Windows 上常见错误是：

```text
[Errno 109] PeekNamedPipe failed
```

这说明 pipe owner 已关闭。此时再连接：

```powershell
python .\codex_ipc_thread_watch.py --enable-control
```

会失败并提示：

```text
连接 IPC 失败：\\.\pipe\codex-ipc
```

直到重新启动 Codex App 或 VSCode Codex 插件。

这回答了一个关键架构问题：如果没有启动 Codex App 或 VSCode 插件，将来的 IPC 请求控制会失败。原因不是 `thread-follower-start-turn` payload 一定错了，而是 IPC 总线和 owner client 不存在；没有 owner 能对 `client-discovery-request` 返回 `canHandle: true`，router 也就无法把 follower request 交给真正的 UI 状态机执行。

因此：

```text
IPC 适合:
  观察/控制当前已打开 UI 中的 live conversation。

IPC 不适合:
  无 UI 后台常驻控制。
  全量线程索引。
  独立创建/运行不依赖 UI owner 的后台任务。
```

如果远控产品希望“即使 App/VSCode 没开也能列线程、读历史、发任务”，必须另接 SDK / app-server 层；IPC 只作为 UI 同步和 UI owner 控制通道。

伪代码：

```js
const payload = Buffer.from(JSON.stringify(message), "utf8");
const frame = Buffer.alloc(4 + payload.length);
frame.writeUInt32LE(payload.length, 0);
payload.copy(frame, 4);
socket.write(frame);
```

初始化请求：

```json
{
  "type": "request",
  "requestId": "<uuid>",
  "sourceClientId": "initializing-client",
  "version": 0,
  "method": "initialize",
  "params": {
    "clientType": "codex-remote-observer"
  },
  "targetClientId": null
}
```

初始化响应：

```json
{
  "type": "response",
  "requestId": "<uuid>",
  "resultType": "success",
  "method": "initialize",
  "handledByClientId": "<client-id>",
  "result": {
    "clientId": "<client-id>"
  }
}
```

## IPC 广播

连接 `codex-ipc` 后，外部进程可以收到实时广播。

已经实测收到：

### `client-status-changed`

当新 client 连接或断开时出现。

```json
{
  "type": "broadcast",
  "method": "client-status-changed",
  "params": {
    "clientId": "<client-id>",
    "clientType": "codex-remote-observer",
    "status": "connected"
  }
}
```

### `thread-stream-state-changed`

这是观察当前 Codex App / VSCode 工作状态最重要的事件。

摘要字段：

```json
{
  "event": "thread-stream-state-changed",
  "hostId": "local",
  "conversationId": "<conversation-id>",
  "changeType": "snapshot",
  "title": "<thread title>",
  "cwd": "C:\\path\\to\\workspace",
  "runtime": "active",
  "turns": 2,
  "latestTurnStatus": "inProgress",
  "latestItem": "command:inProgress ..."
}
```

该事件的完整 payload 中包含：

- `conversationId`
- `hostId`
- `change.type`
- `change.conversationState`
- `conversationState.turns`
- 每个 turn 的 `params`
- turn 状态：`inProgress`、`completed`、`interrupted` 等
- item 列表：`userMessage`、`agentMessage`、`commandExecution`、`reasoning` 等
- `threadRuntimeStatus`
- `cwd`
- `latestCollaborationMode`
- `currentPermissions`
- token usage

这说明外部应用无需读取 SDK 线程历史，也可以实时观察当前 UI 状态。

### snapshot 中可用字段

用户抓到的一份完整 `thread-stream-state-changed` raw payload 说明：当 `change.type = "snapshot"` 时，`change.conversationState` 里会带当前 owner UI 已加载的完整会话状态。它不是一个“主动读取历史”的 IPC API，而是 owner UI 向 IPC 广播自己的内存状态；因此可以把它当作观察和展示来源，但不能假设它永远包含所有历史，尤其是很长会话可能分页。

这份 raw 中最有价值的字段如下：

```text
顶层:
  conversationId
  hostId
  version
  type = thread-stream-state-changed
  change.type = snapshot | patches

conversationState:
  id
  hostId
  turns
  requests
  createdAt
  updatedAt
  title
  latestModel
  latestReasoningEffort
  latestCollaborationMode
  hasUnreadTurn
  rolloutPath
  cwd
  gitInfo
  resumeState
  latestTokenUsageInfo
  currentPermissions
  workspaceKind
  workspaceBrowserRoot
  projectlessOutputDirectory
  source
  turnsPagination
  threadRuntimeStatus
  unreadMessageCount
  threadGoal
```

每个 `turn` 中最有价值的字段：

```text
turn.params
  threadId
  input
  cwd
  model
  effort
  approvalPolicy
  approvalsReviewer
  sandboxPolicy
  attachments
  commentAttachments
  collaborationMode
  serviceTier
  summary
  personality
  outputSchema

turn metadata:
  turnId
  turnStartedAtMs
  durationMs
  finalAssistantStartedAtMs
  status
  error
  diff
  hookRuns
  firstTurnWorkItemStartedAtMs

turn.items:
  userMessage
  agentMessage
  commandExecution
  reasoning
  mcpToolCall
  fileChange
  error
```

最适合远控 UI 展示的字段：

```text
conversationState.title
conversationState.cwd
conversationState.threadRuntimeStatus.type
conversationState.resumeState
conversationState.currentPermissions
conversationState.latestModel
conversationState.latestReasoningEffort
conversationState.latestTokenUsageInfo
conversationState.turns[-1].status
conversationState.turns[-1].items[-1]
conversationState.requests
conversationState.turnsPagination
```

最适合构造远程发送消息的字段：

```text
conversationId
conversationState.cwd
conversationState.currentPermissions
conversationState.latestCollaborationMode
conversationState.latestModel
conversationState.latestReasoningEffort
latest turn.params 中的 approvalPolicy / sandboxPolicy / attachments / summary / personality 等默认值
```

需要小心的字段：

```text
rolloutPath
  指向本机 .codex sessions jsonl，可用于本机调试或补全历史，但不应直接暴露给远端。

turn.params.input 和 turn.items
  含完整用户输入、模型输出、命令、路径、附件上下文，远程同步时需要权限和脱敏策略。

currentPermissions / sandboxPolicy
  决定后续 turn 的权限风险。远程控制时建议默认降权或要求二次确认。

turnsPagination
  如果 hasLoadedOldest=false，说明 snapshot 中未必包含全部历史。
```

### snapshot 与 patches

`thread-stream-state-changed` 不总是全量 snapshot。后续增量通常是：

```json
{
  "change": {
    "type": "patches",
    "patches": [
      {
        "op": "add",
        "path": ["turns", 4, "items", 1],
        "value": {
          "type": "agentMessage",
          "text": "..."
        }
      }
    ]
  }
}
```

这些 patch 是类似 Immer / JSON patch 的结构：

```text
op:   add | replace | remove
path: 数组路径
value: add/replace 的新值
```

因此 bridge 应维护一个本地 `conversationState` cache：

```text
收到 snapshot:
  直接替换 cache[conversationId]

收到 patches:
  对 cache[conversationId] 应用 patch
  如果没有已有 snapshot，只能打印 patch 摘要，不能还原完整状态
```

当前 Python 测试脚本已经实现了这种基本 patch 应用逻辑。

### 完整历史到底从哪里来

观察到的事实：

- IPC snapshot 可以包含当前已加载的 `turns`，看起来像“完整消息记录”。
- 但这本质是 UI 广播状态，不是 IPC 上的“按需读取历史”接口。
- raw payload 中的 `turnsPagination` 表示历史存在分页可能。
- raw payload 中的 `rolloutPath` 指向本机 `.codex\sessions\...\rollout-*.jsonl`，这是另一条本机补全历史的线索。
- 当 Codex CLI 独立工作时，App/VSCode 不会实时收到 IPC 状态；但 App/VSCode 关闭后重新打开，能重新加载到最新消息。这更像是 App/VSCode 启动时从自己的 server / app-server / 本地持久化源刷新历史，而不是 IPC 在后台保存并回放所有事件。
- App/VSCode 都关闭后，IPC 的“最近线程列表”会重置；脚本能列出的只是当前 IPC owner 已广播过的线程，不代表所有历史线程。

所以更准确的判断是：

```text
实时观察:
  用 codex-ipc 的 snapshot + patches。

当前 UI 已加载历史:
  可以直接从 snapshot.conversationState.turns 读取。

严格意义上的完整历史:
  不建议只依赖 IPC。
  更稳的来源是 app-server / SDK 的 thread read/list，或者本机 rollout jsonl。

全量线程列表:
  不建议从 IPC 推断。
  应从 SDK / app-server / 本机会话索引读取，再和 IPC live 状态合并。
```

远控 app 的第一版可以把 IPC snapshot 当作历史展示来源；正式版建议在需要“完整历史”时再接入 app-server 或读取本机 rollout 文件，并做权限隔离。

## IPC 请求与 owner/follower 协议

插件中注册了这些 request handler：

```text
thread-follower-start-turn
thread-follower-compact-thread
thread-follower-steer-turn
thread-follower-interrupt-turn
thread-follower-set-model-and-reasoning
thread-follower-set-collaboration-mode
thread-follower-edit-last-user-turn
thread-follower-command-approval-decision
thread-follower-file-approval-decision
thread-follower-permissions-request-approval-response
thread-follower-submit-user-input
thread-follower-submit-mcp-server-elicitation-response
thread-follower-set-queued-follow-ups-state
```

这些 request 的基本机制：

1. 某个 client 发出 `type: "request"`。
2. IPC router 向其他 client 发送 `client-discovery-request`。
3. 每个 client 根据自己是否能处理该 conversation 返回 `canHandle`。
4. router 把请求转发给能处理的 owner client。
5. owner client 通过 webview postMessage 交给 UI 状态机处理。
6. owner 返回 response。

插件判断 owner 的方式：

```text
getThreadRole(webview, conversationId) === "owner"
```

因此，要远程控制当前 UI 会话，不能只对 app-server 发 `turn/start`。更贴近 App/VSCode 的方式是通过 `codex-ipc` 发 `thread-follower-*` 请求，由当前 owner UI 执行。

已知 version 映射：

```text
thread-stream-state-changed: 6
thread-read-state-changed: 1
thread-archived: 2
thread-unarchived: 1
thread-follower-start-turn: 1
thread-follower-compact-thread: 1
thread-follower-steer-turn: 1
thread-follower-interrupt-turn: 1
thread-follower-set-model-and-reasoning: 1
thread-follower-set-collaboration-mode: 1
thread-follower-edit-last-user-turn: 1
thread-follower-command-approval-decision: 1
thread-follower-file-approval-decision: 1
thread-follower-permissions-request-approval-response: 1
thread-follower-submit-user-input: 1
thread-follower-submit-mcp-server-elicitation-response: 1
thread-follower-set-queued-follow-ups-state: 1
thread-queued-followups-changed: 1
```

`client-status-changed` 默认 version 为 `0`。

### follower request 的实测/反编译参数形状

从 VSCode extension host 与 webview bundle 中继续追踪后，可以看到 IPC request 会被 extension host 转成 webview 消息：

```text
IPC request: thread-follower-start-turn
  -> extension host
  -> webview postMessage: thread-follower-start-turn-request
  -> webview handler: thread-follower-start-turn-for-host
```

对应关系如下：

```text
thread-follower-start-turn
  webview handler:
    thread-follower-start-turn-for-host
  params:
    {
      conversationId,
      turnStartParams
    }
  owner 执行:
    rn(manager, conversationId, { ...turnStartParams })

thread-follower-compact-thread
  params:
    {
      conversationId
    }
  owner 执行:
    manager.compactThread(conversationId)

thread-follower-steer-turn
  params:
    {
      conversationId,
      input,
      restoreMessage,
      attachments
    }
  owner 执行:
    cF(manager, conversationId, input, restoreMessage, attachments)
    -> turn/steer

thread-follower-interrupt-turn
  params:
    {
      conversationId
    }
  owner 执行:
    manager.interruptConversation(conversationId)

thread-follower-set-model-and-reasoning
  params:
    {
      conversationId,
      model,
      reasoningEffort
    }

thread-follower-set-collaboration-mode
  params:
    {
      conversationId,
      collaborationMode
    }

thread-follower-edit-last-user-turn
  params:
    {
      conversationId,
      turnId,
      message,
      agentMode
    }

thread-follower-command-approval-decision
  params:
    {
      conversationId,
      requestId,
      decision
    }
  decision 常见值来自 UI 侧：
    accept / acceptForSession / decline

thread-follower-file-approval-decision
  params:
    {
      conversationId,
      requestId,
      decision
    }
  decision 常见值：
    accept / acceptForSession / decline

thread-follower-permissions-request-approval-response
  params:
    {
      conversationId,
      requestId,
      response
    }

thread-follower-submit-user-input
  params:
    {
      conversationId,
      requestId,
      response
    }
  response 会被传给 replyWithUserInputResponse。

thread-follower-submit-mcp-server-elicitation-response
  params:
    {
      conversationId,
      requestId,
      response
    }

thread-role
  params:
    {
      conversationId
    }
  返回:
    owner / follower 等 role 字符串
```

`thread-follower-start-turn` 的 `turnStartParams` 仍需要继续展开。已从调用路径看到它大致会包含这些字段：

```text
input
cwd
model
effort / reasoningEffort
serviceTier
collaborationMode
approvalPolicy
sandboxPolicy
approvalsReviewer
attachments
commentAttachments
```

更完整的字段来自正常 composer 提交时构造的 turn start params。后续如果要做“远程发送新消息”，建议先记录一次 App/VSCode 正常发送时的 `turnStartParams` 原样 payload，再复刻最小字段集合。

### follower request 外层消息格式

外部 client 发送控制请求时，外层仍是 IPC frame + JSON：

```json
{
  "type": "request",
  "requestId": "<uuid>",
  "sourceClientId": "<已 initialize 得到的 clientId>",
  "version": 1,
  "method": "thread-follower-interrupt-turn",
  "params": {
    "conversationId": "<conversation-id>"
  },
  "targetClientId": null
}
```

如果不指定 `targetClientId`，router 会触发 discovery，由 owner client 返回 `canHandle: true` 后接管。观察脚本目前明确对 discovery 返回 `canHandle: false`，所以它不会误当 owner，也不会抢占当前 UI 会话。

### 通过 IPC 发送新消息的测试路径

要让当前 App / VSCode owner 发起一轮新对话，外部 client 可以发送：

```text
method: thread-follower-start-turn
version: 1
params:
  conversationId
  turnStartParams
```

其中 `turnStartParams` 可以参考最近一个正常 turn 的 `turn.params`，再替换：

```json
{
  "threadId": "<conversationId>",
  "input": [
    {
      "type": "text",
      "text": "要发送的用户消息\n",
      "text_elements": []
    }
  ],
  "cwd": "c:\\test",
  "approvalPolicy": "never",
  "approvalsReviewer": "user",
  "sandboxPolicy": {
    "type": "dangerFullAccess"
  },
  "attachments": [],
  "commentAttachments": [],
  "collaborationMode": {
    "mode": "default",
    "settings": {
      "model": "gpt-5.5",
      "reasoning_effort": "xhigh",
      "developer_instructions": null
    }
  },
  "model": null,
  "effort": null,
  "serviceTier": null,
  "summary": "none",
  "personality": "friendly",
  "outputSchema": null
}
```

安全注意：

- 这会真正让 owner UI 调用自己的 app-server 开始 turn。
- 不应在未确认的情况下复用 `dangerFullAccess`。
- 测试脚本默认仍然只读，必须加 `--enable-control` 才能使用 `send <文本>`。
- 发送前脚本会打印 cwd、approvalPolicy、sandboxPolicy 并要求输入 `yes`。

## 已创建的验证脚本

新增文件：

`C:\Users\pengy\project\codex-remote\codex_ipc_observer.js`

用途：

- 连接 `\\.\pipe\codex-ipc`
- 发送 `initialize`
- 接收 IPC frame
- 默认输出摘要
- `--raw` 输出原始消息
- 自动响应 `client-discovery-request` 为 `canHandle: false`，避免误接管当前会话

用法：

```powershell
node C:\Users\pengy\project\codex-remote\codex_ipc_observer.js --duration-ms=5000
node C:\Users\pengy\project\codex-remote\codex_ipc_observer.js --once
node C:\Users\pengy\project\codex-remote\codex_ipc_observer.js --raw
```

示例摘要输出：

```json
{
  "event": "thread-stream-state-changed",
  "hostId": "local",
  "conversationId": "<conversation-id>",
  "changeType": "snapshot",
  "title": "验证 Codex CLI 同步机制",
  "cwd": "C:\\Users\\pengy\\project\\codex-remote",
  "runtime": "active",
  "turns": 2,
  "latestTurnStatus": "inProgress",
  "latestItem": "command:inProgress ..."
}
```

新增文件：

`C:\Users\pengy\project\codex-remote\codex_ipc_thread_watch.py`

用途：

- 连接 `\\.\pipe\codex-ipc`
- 先收集并列出最近从 IPC 收到的线程
- 选择线程后进入监听模式
- `h/history` 打印 snapshot 中已加载的 turns
- `raw` 打印该线程最近一次 raw payload
- `--enable-control` 后支持 `send <文本>`，通过 `thread-follower-start-turn` 让 App/VSCode owner 发起新 turn
- App/VSCode owner 关闭导致 pipe 断开时，会给出明确提示；加 `--reconnect` 可以等待 owner 重启后自动重连

用法：

```powershell
python -u C:\Users\pengy\project\codex-remote\codex_ipc_thread_watch.py --collect-seconds=1 --list-once
python -u C:\Users\pengy\project\codex-remote\codex_ipc_thread_watch.py --collect-seconds=5 --enable-control
python -u C:\Users\pengy\project\codex-remote\codex_ipc_thread_watch.py --collect-seconds=5 --enable-control --reconnect
python -u C:\Users\pengy\project\codex-remote\codex_ipc_thread_watch.py --conversation-id <conversationId> --enable-control --reconnect
```

断线语义：

```text
App/VSCode 至少一个 owner 运行:
  可以连接 pipe，可以观察/控制 owner 能 handle 的线程。

App/VSCode 都关闭:
  pipe 消失或断开，脚本无法通过 IPC 控制。
  --reconnect 只能等待 owner 重新出现，不能自己创建 IPC owner。
```

## 和 SDK 方式的区别

### Python SDK

优点：

- API 清晰。
- 适合启动自己的线程。
- 适合脚本化创建、读取、运行 Codex turn。

缺点：

- 默认启动新的 bundled app-server。
- 不直接加入 App / VSCode UI owner/follower 实时通道。
- CLI/SDK 发出的消息未必会实时同步到当前 App UI。

### `codex-ipc`

优点：

- 能观察当前 Codex App / VSCode UI 的实时线程状态。
- 能看到 active/idle、turn、item、commandExecution 等。
- 是 App/VSCode 多窗口协同的核心本地通道。

缺点：

- 没有公开 SDK。
- 协议是内部实现，可能随版本变化。
- 控制能力需要精确实现 `thread-follower-*` params schema。
- 不应随便发控制请求，避免误操作当前会话。

## 推荐远控 app 架构

### 总体形态：双通道

```text
本机 bridge
  -> SDK / app-server
       - 全量线程列表
       - 完整历史读取
       - 无 UI 时的后台 Codex 任务
  -> codex-ipc
       - 当前 App/VSCode UI live 状态
       - snapshot + patches 实时流
       - thread-follower-* 控制当前 UI owner
  -> StateStore 合并两边状态
  -> WebSocket/SSE/HTTP API
  -> 手机或远端 Web UI
```

核心原则：

- `SDK / app-server` 是“事实库 / 后台执行层”。
- `codex-ipc` 是“UI 实时同步 / UI owner 控制层”。
- 两者要合并展示，但不要互相假装成对方。

### 第一阶段：只读观察

```text
本机 bridge
  -> 先用 SDK / app-server 或本机会话索引列出全量线程
  -> 再连接 \\.\pipe\codex-ipc 监听 thread-stream-state-changed
  -> 给线程打 live / stale / offline 标记
  -> 过滤/整理状态后通过 WebSocket/SSE/HTTP API 输出
```

推荐展示字段：

- 活跃线程列表
- 每个线程的 title / cwd / runtime
- 最新 turn 状态
- 最新 agent/user/command item
- 是否等待用户输入
- 是否正在执行命令
- token usage 简要信息

### 第二阶段：控制当前线程

实现 IPC request：

- `thread-follower-start-turn`
- `thread-follower-steer-turn`
- `thread-follower-interrupt-turn`
- `thread-follower-submit-user-input`
- `thread-follower-command-approval-decision`
- `thread-follower-file-approval-decision`

需要先从 webview bundle 中继续提取 params schema。

控制前必须检查：

- 本机是否能连接 `\\.\pipe\codex-ipc`。
- 是否存在 App/VSCode owner。
- owner 是否能 handle 目标 `conversationId`。
- 当前 thread 是否 idle，或者该请求是否允许在 active turn 中发送。

如果 IPC 不可用，则 UI 控制按钮应显示为不可用；此时可以退回 SDK/app-server 路径创建后台 turn，但那一轮不一定会实时出现在已经关闭的 App/VSCode UI 中。

### 第三阶段：远程安全层

远控 app 不应直接把 named pipe 暴露到网络。

建议：

- 本机 bridge 监听 localhost 或 Unix/Windows 本地通道。
- 远端访问必须有 token。
- 控制操作必须二次确认。
- 默认只读。
- 对 `start-turn`、`interrupt`、`approval-decision` 做审计日志。

### 建议的 bridge 最小实现

本地 bridge 可以从当前 `codex_ipc_observer.js` 演进，分成三层：

```text
IpcClient
  - connect \\.\pipe\codex-ipc
  - initialize
  - frame encode/decode
  - request / response / broadcast dispatch

StateStore
  - 按 conversationId 保存最新 thread-stream-state-changed
  - 提取 title/cwd/runtime/latestTurn/latestItem/waitingForApproval
  - 可选：只保留摘要，避免把敏感完整上下文同步到远端

Http/WebSocket API
  - GET /threads
  - GET /threads/:conversationId
  - WS /events
  - POST /threads/:conversationId/interrupt
  - POST /threads/:conversationId/steer
```

第一版建议只实现 `GET /threads` 和 `WS /events`。控制接口单独加开关，例如 `CODEX_REMOTE_ENABLE_CONTROL=1`，且要求 token。

## 后续待研究

1. 展开 `thread-follower-start-turn` 的完整 `turnStartParams` schema。
2. 记录一次正常 composer 发送消息时的最小 payload，用于安全复刻远程 start-turn。
3. 尝试只对测试线程发 follower request，验证 owner UI 是否响应。
4. 做一个本地 bridge server，把 `codex_ipc_observer.js` 扩展成 WebSocket API。
5. 对比 Codex App 本体 bundle，确认它是否和 VSCode 使用同一套 `codex-ipc` client 逻辑。
6. 研究 `codex app-server daemon enable-remote-control`，判断它和 `codex-ipc` 的关系。
7. 做版本兼容保护：当 `thread-stream-state-changed` version 或 follower method version 变化时，bridge 应拒绝控制请求，只保留只读观察。

## 当前判断

原先的猜想“SDK 本质是 CLI/app-server 方式调用 Codex”是正确的，但不完整。

更准确的模型是：

```text
SDK / CLI:
  独立 app-server runtime
  适合脚本化运行 Codex

VSCode:
  独立 app-server runtime
  webview 通过 extension host 转发 app-server JSON-RPC
  同时加入 codex-ipc 做 UI 会话协同

Codex App:
  使用自身 runtime
  也加入 codex-ipc 做多窗口/多客户端协同

远控观察当前 UI:
  应接 codex-ipc
```

因此，远控 app 的验证方向已经从“SDK 能不能读线程”转向“本机 bridge 能不能稳定消费 `codex-ipc` 广播并安全发送 follower request”。

## 2026-06-02 追加结论：会话事实源与发送路由

进一步分析 VSCode 插件和 activation probe 后，当前模型更新如下：

```text
app-server / SDK / 本地持久化:
  是会话事实源。
  Codex App / VSCode 每次激活历史线程时，会通过自己的 app-server 重新 read/resume 上下文。

codex-ipc:
  是 live UI owner 的同步与控制通道。
  它广播当前 UI 已加载的 snapshot/patches，也能把 follower request 转交给 owner。
  它不是后台事实库，也不负责保存或主动唤醒所有历史会话。
```

VSCode webview 中激活历史线程的内部入口是 `maybe-resume-conversation`，实际会调用：

```text
thread/read
thread/turns/list 或 thread/read includeTurns=true
thread/resume
```

resume 成功后，VSCode 会：

```text
resumeState = resumed
setConversationStreamRole(role = owner)
markConversationStreaming(conversationId)
broadcastConversationSnapshot(conversationId)
```

这说明手动在 App/VSCode 中打开 history-only 线程后，它会自然变成 live，并通过 `thread-stream-state-changed` 广播完整 snapshot。远控端不需要伪造 IPC broadcast 来“唤醒”它。

新的发送路由应采用 auto 策略：

```text
发送消息到 conversationId:
  1. 先检查 StateStore 是否有该 conversationId 的 live IPC snapshot / owner。
  2. 如果存在 live owner 且线程允许 start-turn：
       走 IPC request: thread-follower-start-turn。
       后续输出由 IPC snapshot/patches 同步。
  3. 如果不存在 live owner，即 history-only 或 stale：
       走 SDK thread_resume(...).turn(...) / run(...)。
       后续输出由本机 bridge 自己消费 SDK stream，并推给 Web UI。
  4. 如果用户之后在 App/VSCode 中打开该线程：
       App/VSCode 会从 app-server / 本地历史重新读取上下文。
       bridge 收到 IPC snapshot 后，将该线程升级为 live。
```

重要约束：

- 不建议外部进程伪造 `thread-stream-state-changed` 或 `thread-follower-start-turn` broadcast。
- `thread-follower-start-turn` request 需要已有 owner 能通过 `thread-role` 判定；history-only 未被 UI resume 前通常不会有 owner。
- live 且 busy 的线程不应退回 SDK 并发推进；应优先考虑 steer、interrupt、排队或提示用户。
- SDK 续聊不是“绕过”事实源，而是使用同一类 app-server 会话事实源进行后台 turn；只是它不实时驱动当前 App/VSCode UI。
