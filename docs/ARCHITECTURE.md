# PocketCodex 架构说明

本文描述 PocketCodex 当前实现的组件边界、数据流和安全模型。它反映的是仓库中的实际代码，而不是未来设想。

## 1. 系统边界

PocketCodex 是运行在用户桌面电脑上的轻量 HTTP 服务。手机浏览器通过安全隧道访问该服务，服务再读取本机 Codex session 记录并启动 Codex CLI 子进程。

它不包含云端业务服务器，不把项目文件同步到 PocketCodex 自有云端，也不控制 Codex 桌面图形界面的窗口。

```mermaid
flowchart TB
    subgraph Mobile[手机]
        Browser[移动浏览器<br/>HTML/CSS/JavaScript]
    end

    subgraph Network[远程访问层]
        Cloudflare[Cloudflare Quick Tunnel<br/>国内用户默认入口]
        Tailscale[Tailscale Serve<br/>私有网络备选]
    end

    subgraph Desktop[桌面电脑]
        HTTP[PocketCodex HTTP 服务<br/>127.0.0.1:8765]
        Auth[令牌认证]
        SessionStore[SessionStore]
        FolderBrowser[FolderBrowser]
        RunManager[RunManager]
        SessionFiles[~/.codex/sessions/*.jsonl]
        Roots[新 session 允许的起始目录]
        Workspaces[本机 session 工作目录]
        Codex[Codex CLI 子进程]
        Uploads[.remote_uploads]
    end

    Browser -->|HTTPS + X-Remote-Codex-Token| Tailscale
    Browser -->|HTTPS + X-Remote-Codex-Token| Cloudflare
    Tailscale --> HTTP
    Cloudflare --> HTTP
    HTTP --> Auth
    Auth --> SessionStore
    Auth --> FolderBrowser
    Auth --> RunManager
    SessionStore --> SessionFiles
    FolderBrowser --> Roots
    RunManager --> Codex
    RunManager --> Uploads
    Codex --> Workspaces
    Roots -.选择新 session cwd.-> Workspaces
```

## 2. 组件职责

### 移动 Web 客户端

目录：`remote_web/`

- 调用 `/api/sessions` 展示最近 session。
- 调用 `/api/runs` 继续已有 session。
- 调用 `/api/sessions/new` 在选定工作目录中新建 session。
- 每 2.5 秒轮询当前 run，每 5 秒刷新 session 列表。
- 在浏览器端压缩图片并随 JSON 请求上传。
- 从 URL fragment 读取首次访问令牌，保存到浏览器 `localStorage`，后续通过 `X-Remote-Codex-Token` 请求头发送。

### HTTP 服务与认证

文件：`remote_codex_server.py`

- 使用 Python 标准库 `ThreadingHTTPServer` 提供静态文件和 JSON API。
- 默认仅监听 `127.0.0.1:8765`。
- 首次启动生成至少 24 字符的随机访问令牌并写入 `remote.env`。
- 对 API 使用常量时间比较验证请求头、查询参数或 Cookie 中的令牌。
- 对静态响应和 API 响应设置 `no-store`，并禁止页面被嵌入 iframe。

### SessionStore

- 扫描 `~/.codex/sessions` 中最近修改的 `rollout-*.jsonl`。
- 读取 session ID、工作目录、用户指令和最近的 assistant 响应。
- 忽略注入的系统上下文，只提取实际用户指令。
- 默认返回最近 30 个 session。

### FolderBrowser

- 维护允许新建 session 的项目根目录白名单。
- 默认根目录是当前用户的 `Desktop` 和 `Documents`。
- `REMOTE_CODEX_ROOTS` 可覆盖默认根目录。
- 拒绝不存在、不是目录或逃逸到白名单外的路径。
- 只列出非隐藏子目录，单层最多 250 个。

### RunManager

- 新任务执行 `codex exec --skip-git-repo-check -`。
- 已有任务执行 `codex exec resume --skip-git-repo-check <SESSION_ID> -`。
- 用户指令通过标准输入传给 Codex。
- 使用 session 的原始工作目录作为子进程 `cwd`。
- 捕获标准输出并返回移动端。
- 维护 queued、running、completed、failed、cancelled 等运行状态。
- Windows 下使用独立进程组，以便停止任务。

### 图片上传

- 浏览器先缩放和压缩图片。
- 服务端重新验证 Base64、图片签名、数量和大小。
- 每次最多 4 张，每张最大 8 MB，仅接受 JPEG、PNG、WebP。
- 文件临时保存在 `.remote_uploads/`，并通过 Codex CLI 的 `--image` 参数传入。
- run 正常结束、失败或取消后的 `finally` 阶段会删除图片；服务进程异常退出时可能遗留文件。

## 3. 关键请求流程

### 继续已有 session

```mermaid
sequenceDiagram
    participant U as 手机用户
    participant W as 移动 Web
    participant S as PocketCodex 服务
    participant F as ~/.codex/sessions
    participant C as Codex CLI

    U->>W: 打开页面
    W->>S: GET /api/sessions
    S->>F: 扫描并解析最近 session
    F-->>S: session 元数据
    S-->>W: session 列表
    U->>W: 选择 session 并发送指令
    W->>S: POST /api/runs
    S->>S: 校验令牌和 session ID
    S->>C: codex exec resume ID -
    C-->>S: stdout / 状态
    loop 任务运行中
        W->>S: GET /api/runs/{id}
        S-->>W: 当前状态与输出
    end
```

### 新建 session

```mermaid
sequenceDiagram
    participant U as 手机用户
    participant W as 移动 Web
    participant S as PocketCodex 服务
    participant D as 项目目录
    participant C as Codex CLI

    W->>S: GET /api/folders
    S-->>W: 允许的项目根目录
    U->>W: 进入目录并输入第一条指令
    W->>S: POST /api/sessions/new
    S->>S: 解析真实路径并验证白名单
    S->>C: codex exec - (cwd=项目目录)
    C->>D: 读取或修改项目文件
    C-->>S: 输出 session ID 与结果
    S-->>W: 新 session 状态
```

## 4. 网络模型

### Cloudflare Quick Tunnel

面向国内用户的默认上手路径：

- cloudflared 从本机主动建立到 Cloudflare 的出站连接。
- 手机不需要安装 Tailscale，但必须能够访问生成的 `trycloudflare.com` 地址。
- 部分国内 iPhone 用户会通过已经安装的小火箭（Shadowrocket）等代理工具访问；这些网络工具不属于 PocketCodex。
- 随机 URL 可从公网访问，PocketCodex 令牌成为主要应用层防线。
- URL 重启后通常改变，不适合作为固定服务地址。
- 当前方案没有 Cloudflare Access 身份策略，不应视为私有网络。

### Tailscale Serve

无法或不便使用 Tailscale 的用户不需要安装它。已经具备 Tailscale 条件、并希望获得固定私有入口时，可以选择该方案：

- PocketCodex 仍只监听 loopback。
- Tailscale 在 tailnet 内提供 HTTPS 入口。
- tailnet 身份和 ACL 构成令牌以外的第二层访问控制。
- 手机和电脑都需要加入同一 tailnet。

## 5. 安全边界

### 已有控制

- 默认仅监听 `127.0.0.1`。
- API 需要随机令牌，比较使用 `hmac.compare_digest`。
- session 只能从本机 session 存储中选择，不能提交任意 session ID。
- 新 session 的工作目录受根目录白名单约束。
- 图片数量、大小、格式和整体请求大小受限。
- 响应禁止缓存，并设置基础浏览器安全头。

### 不在保证范围内

- 令牌不是多用户账户系统，没有权限角色、过期时间或设备撤销列表。
- 拿到有效令牌的调用方可以向允许的 Codex session 发送任意自然语言指令。
- 根目录白名单只约束新 session 的起始目录，不约束已有 session，也不是 Codex 的文件系统沙箱。
- Codex 最终可执行的文件和命令范围仍由 Codex CLI 自身的 sandbox、approval policy 和本机权限决定。
- 远程链路使用非交互 `codex exec`，不能依赖交互式 `PermissionRequest` hook 保护这些任务。
- Quick Tunnel 随机 URL 不是访问控制。
- 上传图片在 run 收尾阶段自动清理，但服务异常终止可能遗留文件；运行输出仅保存在进程内存中。
- 进程内 run 状态在服务重启后不会恢复。
- 停止进程不会回滚 Codex 已经完成的文件修改。

### 部署原则

1. 保持服务绑定 loopback。
2. 使用默认 Quick Tunnel 时，把令牌和完整 URL 当作密码；不用时停止隧道。
3. 把 `remote.env` 当作密码文件处理。
4. 只开放必要项目根目录。
5. 令牌疑似泄漏时立即轮换。
6. 能够使用 Tailscale 时，可通过最小范围 ACL 获得额外隔离。
7. 不在不受信任或多人共用电脑上长期运行。

## 6. 可选通知链路

通知与审批脚本是旁路组件，不参与核心 API 请求：

```mermaid
flowchart LR
    Codex[Codex / Claude Code] -->|Hook event| Hook[watch_done.py / watch_approve.py]
    Hook --> N[ntfy]
    Hook --> P[Pushcut]
    N --> Phone[手机 / Wear OS]
    P --> Apple[iPhone / Apple Watch]
    Phone -.审批结果.-> Hook
    Apple -.审批结果.-> Hook
    Hook -.allow / deny.-> Codex
```

核心远程控制不要求安装或配置这套链路。`watch_done.py` 可用于完成提醒；`watch_approve.py` 面向交互式桌面 Codex/Claude Code，不应被描述为 PocketCodex 非交互任务的审批安全边界。详情见 [NOTIFICATIONS.md](./NOTIFICATIONS.md)。

## 7. 后续架构方向

- 将启动、隧道和配置检查收敛为跨平台 CLI。
- 用可撤销、可过期的设备凭证替代单一长期令牌。
- 增加持久化 run 记录和上传文件清理策略。
- 增加结构化日志、健康状态和诊断导出。
- 为反向代理部署补充明确的可信代理和安全头策略。
- 保持核心服务与通知/审批插件解耦。
