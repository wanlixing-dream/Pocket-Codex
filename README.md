<div align="center">

# PocketCodex

**在手机浏览器里连接并继续桌面版 Codex 的任务。**

[![CI](https://github.com/wanlixing-dream/Pocket-Codex/actions/workflows/ci.yml/badge.svg)](https://github.com/wanlixing-dream/Pocket-Codex/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](./LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Dependencies](https://img.shields.io/badge/Python_dependencies-none-brightgreen)

**[English](./README.en.md)** · **[架构说明](./docs/ARCHITECTURE.md)** · **[通知与审批](./docs/NOTIFICATIONS.md)**

</div>

> [!IMPORTANT]
> PocketCodex 是社区维护的非官方项目，与 OpenAI 没有隶属或背书关系。
> 它连接 Windows Codex 桌面 App 随附的 `app-server`，不要求另外安装命令行版本，也不是桌面画面的远程屏幕镜像。

## PocketCodex 能做什么

- 在手机上查看桌面版 Codex 最近的任务。
- 选择已有任务，向同一个 thread 发送下一条文字指令。
- 在指定项目文件夹中新建桌面版 Codex 任务。
- 从手机上传 JPEG、PNG 或 WebP 图片给 Codex 分析。
- 查看执行状态、耗时与输出，并可停止当前任务。
- 使用手机浏览器的语音识别输入指令。
- 可选接入 ntfy / Pushcut，在手机或手表接收完成通知和审批请求。

手机端不直接运行 Codex。所有 thread、项目文件和推理执行都留在你的桌面电脑上。

## 工作原理

```mermaid
flowchart LR
    Phone[手机浏览器] -->|HTTPS| Access{远程访问层}
    Access -->|国内用户默认| CF[Cloudflare Quick Tunnel]
    Access -->|私有网络备选| TS[Tailscale Serve]
    TS --> Server[PocketCodex 本地服务<br/>127.0.0.1:8765]
    CF --> Server
    Server --> Bridge[连接桌面 App 随附 app-server]
    Bridge -->|thread/start| New[新建桌面任务]
    Bridge -->|thread/resume + turn/start| Resume[继续已有任务]
    New --> Store[桌面 App thread 存储]
    Resume --> Store
    Store --> Desktop[Windows Codex 桌面 App]
    Bridge --> Workspace[本地项目文件夹]
    Server -.可选.-> Notify[ntfy / Pushcut 通知]
```

详细的组件职责、请求流程和安全边界见 [架构说明](./docs/ARCHITECTURE.md)。

## 系统要求

### 桌面电脑

- Windows 10/11。当前版本对接 Microsoft Store 的 Windows Codex 桌面 App。
- Python 3.10 或更高版本。
- 已从 Microsoft Store 安装并登录 **Windows Codex 桌面 App**。
- 至少已有一个桌面版 Codex 任务，或者准备一个用于新建任务的项目文件夹。
- 以下远程访问工具任选一个：
  - 默认上手：[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
  - 私有网络备选：[Tailscale](https://tailscale.com/download)

### 手机

- Safari、Chrome 或其他现代浏览器。
- 使用 Cloudflare Quick Tunnel 时，手机需要能够访问生成的 `trycloudflare.com` 地址。部分国内 iPhone 用户会使用已经安装的小火箭（Shadowrocket）等代理工具；PocketCodex 本身不提供代理服务。
- 使用 Tailscale 备选方案时，手机也需要安装 Tailscale 并登录同一账号。

PocketCodex 的 Python 服务只使用标准库，不需要运行 `pip install`。

## 五分钟开始使用

### 1. 获取项目

```powershell
git clone https://github.com/wanlixing-dream/Pocket-Codex.git
cd Pocket-Codex
```

### 2. 检查本地环境

```powershell
python --version
```

先打开 Codex 桌面 App，完成登录，并确认它能在本机正常创建任务。PocketCodex 会自动发现桌面 App 随附的 app-server，不需要把 `codex` 命令加入 `PATH`。

### 3. 启动 PocketCodex

```powershell
python .\remote_codex_server.py
```

服务默认只监听：

```text
http://127.0.0.1:8765
```

首次启动会在项目目录生成私有配置 `remote.env`，其中包含随机访问令牌。终端会打印一条带令牌的本地地址，先在电脑浏览器打开它，确认可以看到桌面任务列表。

> [!WARNING]
> 不要提交、截图或公开分享 `remote.env`。任何拿到令牌的人都可能通过 PocketCodex 向你的桌面版 Codex thread 发送指令。

### 4. 让手机连接电脑

面向国内用户，默认使用 Cloudflare Quick Tunnel。它不要求手机安装 Tailscale；如果当前网络无法直接访问生成的地址，请使用你已有且符合当地规定的网络环境。

#### 方案 A：Cloudflare Quick Tunnel（默认）

1. 在电脑安装 `cloudflared`。
2. 保持 PocketCodex 服务运行，另开一个 PowerShell。
3. 执行：

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

4. cloudflared 会显示一个临时 `https://*.trycloudflare.com` 地址。
5. 在该地址末尾添加首次启动时生成的令牌：

```text
https://随机地址.trycloudflare.com/#token=你的_REMOTE_CODEX_TOKEN
```

6. 用手机打开该地址。页面会把令牌保存在当前浏览器中，并从地址栏移除令牌片段。

Quick Tunnel 地址通常会在 cloudflared 重启后改变。该地址可从公网访问，访问令牌是主要的应用层防线，不要把完整链接发到群聊、Issue 或截图中。

#### 方案 B：Tailscale Serve（私有网络备选）

Tailscale 的设备身份和 tailnet ACL 提供了额外隔离，适合已经能够安装 Tailscale、并希望长期使用固定私有地址的用户：

1. 在电脑和手机安装 Tailscale，并登录同一账号。
2. 保持 PocketCodex 服务运行。
3. 在电脑执行：

```powershell
tailscale serve --bg http://127.0.0.1:8765
tailscale serve status
```

4. 复制状态中显示的 HTTPS 地址，并在首次访问时添加令牌：

```text
https://your-device.your-tailnet.ts.net/#token=你的_REMOTE_CODEX_TOKEN
```

停止 Tailscale 共享：

```powershell
tailscale serve reset
```

### 5. 从手机开始工作

1. 从“最近任务”选择一个桌面版 Codex thread。
2. 输入下一条指令，也可以附加最多 4 张图片。
3. 点击发送，PocketCodex 会通过桌面 App 随附的 app-server 继续同一个 thread。
4. 点击右上角的 `+`，选择项目目录并输入第一条指令，可新建桌面版任务。
5. 任务运行期间可以查看状态或点击停止。

## 配置可选项目目录

新建桌面任务时，文件夹选择器默认只允许访问当前用户的 `Desktop` 和 `Documents`。这是目录白名单，不是文件夹没有刷新。

如需显示其他项目目录，先完成一次首次启动，让服务生成安全令牌；然后在 `remote.env` 增加 `REMOTE_CODEX_ROOTS`。Windows 使用分号分隔多个目录：

```dotenv
REMOTE_CODEX_ROOTS=C:\Users\你的用户名\Desktop;C:\Users\你的用户名\source;D:\Projects
```

修改后需要重启 PocketCodex 服务。服务只允许浏览和选择这些根目录及其子目录；普通文件、隐藏目录以及白名单外路径不会出现在选择器中。该白名单只约束新建任务的起始目录，不限制已有 thread，也不是 Codex 的文件系统沙箱。

## 配置文件

`remote.env` 由服务首次启动时自动创建，也可以手动填写：

```dotenv
# 至少 24 个字符；建议使用随机生成的长令牌
REMOTE_CODEX_TOKEN=replace-with-a-long-random-token

# 可选：允许新建桌面任务的项目根目录
REMOTE_CODEX_ROOTS=C:\Users\you\Desktop;D:\Projects
```

可以从 [`remote.env.example`](./remote.env.example) 开始配置。真实的 `remote.env` 已被 `.gitignore` 排除。

## 可选：手机/手表通知与审批

`watch_done.py` 和 `watch_approve.py` 是可选增强，不影响 PocketCodex 的核心远程控制功能：

- `watch_done.py`：任务完成、失败或额度接近上限时发送通知。
- `watch_approve.py`：通过 Codex/Claude Code hook 把支持的审批请求发送到手机或手表。
- ntfy 可直接用于 Android、Wear OS 和手机通知。
- Pushcut 可为 iPhone / Apple Watch 提供更完整的交互通知。

完整配置步骤见 [通知与审批](./docs/NOTIFICATIONS.md)。

## 安全说明

PocketCodex 可以在你的电脑上启动 Codex 并访问允许的项目目录，应把它视为远程管理入口：

- 服务默认绑定 `127.0.0.1`；不要直接改成 `0.0.0.0` 暴露到局域网或公网。
- 默认 Quick Tunnel 是公网入口；令牌等同密码，完整访问链接只能自己保存。
- 能够使用 Tailscale 时，可用其设备身份和 ACL 增加一层私有网络隔离。
- 不要公开分享带 `#token=` 或 `?token=` 的链接。
- 如果链接或令牌可能泄漏，停止服务，删除 `remote.env` 后重新启动以生成新令牌。
- 仅把 `REMOTE_CODEX_ROOTS` 指向确实需要远程工作的目录。
- `REMOTE_CODEX_ROOTS` 只限制新建任务的文件夹选择器，不能限制已有 thread 或 Codex 后续能够访问的路径。
- PocketCodex 只允许继续桌面 App 本地记录中存在的 thread，但发送给 Codex 的指令仍可能修改项目文件或运行命令。
- PocketCodex 使用独立的 app-server 连接。需要人工审批的请求不会自动出现在另一个已打开的桌面窗口中；当前版本会拒绝未支持的交互请求并提示回到电脑处理。
- Cloudflare Quick Tunnel 不建议在无人看管时长期运行；不用时应停止 cloudflared 和 PocketCodex 服务。

更多威胁边界见 [架构说明：安全边界](./docs/ARCHITECTURE.md#安全边界)。

## 当前限制

- PocketCodex 与桌面窗口共享持久化 thread，但不是桌面画面的实时镜像。不要同时从手机和桌面窗口向同一个 thread 发送任务。
- 桌面 App 的 `app-server` 是内部接口，Codex 更新后可能需要同步更新 PocketCodex 的兼容层。
- 服务运行期间同一个 thread 只能有一个 PocketCodex 任务执行。
- 任务列表来自桌面 App 的 `thread/list`，默认最多读取最近 30 个。
- 每条消息最多上传 4 张图片，每张不超过 8 MB。
- 单次运行最长 6 小时，页面保留该次运行输出的最后 30,000 个字符。
- 文件夹选择器只显示目录，单层最多显示 250 个非隐藏子目录。
- 图片会在任务正常收尾时删除；服务进程异常退出时可能遗留在 `.remote_uploads/`。
- 停止任务只会终止进程，不会回滚 Codex 已完成的文件修改。
- `start_remote_codex.ps1` 是面向当前 Windows + cloudflared + ntfy 环境的便捷脚本；通用安装建议先使用本 README 中的手动命令。

## 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 手机显示 `Unauthorized` | 从包含正确 `#token=` 的地址重新打开；令牌轮换后清除该站点的浏览器存储 |
| 看不到桌面/文档之外的项目 | 在首次启动生成的 `remote.env` 中配置 `REMOTE_CODEX_ROOTS`，然后重启服务 |
| 任务列表为空 | 先在 Codex 桌面 App 中完成至少一个任务，并确认桌面 App 使用的是当前 Windows 用户 |
| 手机无法连接 | 确认 Python 服务仍在运行，再检查 `tailscale serve status` 或 cloudflared 终端 |
| 找不到 Codex 桌面版 | 从 Microsoft Store 安装并启动 Codex；也可通过 `REMOTE_CODEX_DESKTOP_EXE` 指定桌面 App 随附的 `codex.exe` |
| 远程任务遇到审批或权限问题 | 回到 Codex 桌面 App 处理；当前远程端不会自动批准敏感操作 |

## 项目结构

```text
Pocket-Codex/
├── remote_codex_server.py   # HTTP API、认证、桌面 App 发现与 app-server 连接
├── remote_web/              # 手机端 HTML/CSS/JavaScript
├── start_remote_codex.ps1   # Windows 自动启动与 Quick Tunnel 辅助脚本
├── watch_approve.py         # 可选：远程审批 hook
├── watch_done.py            # 可选：完成/失败通知 hook
├── examples/                # Codex 与 Claude Code hook 配置示例
├── docs/                    # 架构和可选功能文档
└── tests/                   # 标准库 unittest 测试
```

## 开发与测试

```powershell
python -m unittest discover -s tests -v
python -m py_compile remote_codex_server.py watch_approve.py watch_done.py
```

核心单元测试不需要网络；桌面 App 发现和端到端连接验证在 Windows 上进行。

## 开源路线

接下来适合优先完善：

- 统一的跨平台启动与配置向导。
- 可选择的 thread 数量和项目根目录管理界面。
- 更完善的运行日志、清理策略和错误诊断。
- Tailscale/Cloudflare 状态检查与一键连接。
- 对移动端交互和安全边界的端到端测试。

欢迎提交 Issue 和 Pull Request。涉及认证、目录访问或命令执行的改动，请同时说明威胁模型与验证方式。

## License

[MIT](./LICENSE)
