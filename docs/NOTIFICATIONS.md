# 可选通知与远程审批

PocketCodex 的手机远程控制不依赖本页功能。只有需要任务完成提醒、额度预警或手机/手表审批时，才需要配置 `watch_done.py` 和 `watch_approve.py`。

## 组件选择

| 需求 | 推荐组件 |
| --- | --- |
| Android / Wear OS 通知 | ntfy app |
| iPhone 通知 | ntfy app 或 Pushcut |
| Apple Watch 交互通知 | Pushcut |
| 任务完成与额度提醒 | `watch_done.py` |
| 支持的远程审批 | `watch_approve.py` |
| 核心手机远程控制 | 不需要 ntfy 或 Pushcut |

脚本只使用 Python 标准库。

## 1. 准备通知通道

### ntfy

1. 安装 [ntfy](https://ntfy.sh/) 手机应用。
2. 生成一个足够长、不可猜测的 topic 名称。
3. 在手机订阅该 topic。

公共 `ntfy.sh` 的 topic 名称本身接近共享密钥，不要使用项目名、手机号等容易猜到的值。

### Pushcut（可选）

Apple Watch 需要更完整的交互按钮时：

1. 在 iPhone 和 Apple Watch 安装 [Pushcut](https://www.pushcut.io/)。
2. 在 Pushcut 创建一个 Notification。
3. 从 Pushcut Account -> API 获取 API key。
4. 动态操作和部分高级能力可能需要 Pushcut Pro。

## 2. 创建本地配置

复制示例文件：

```powershell
Copy-Item .\watch.env.example .\watch.env
```

至少填写通知通道所需的值。ntfy 的发送 topic 和订阅 topic 默认可以使用同一个值；如果你把通知和审批拆成两个 topic，再分别填写：

```dotenv
# ntfy 通知 topic；watch_done.py 和普通通知使用
NTFY_NOTIFY_TOPIC=your-long-random-topic

# ntfy 审批 topic；不填时通常回退到 NTFY_NOTIFY_TOPIC
NTFY_TOPIC=your-long-random-topic

# 使用 Pushcut 时填写
PUSHCUT_KEY=your-pushcut-api-key
PUSHCUT_NOTIF=your-notification-name
```

真实 `watch.env` 已被 `.gitignore` 排除，不应提交。

## Quick Tunnel 新链接通知

跨平台辅助脚本 `start_remote_codex.py` 可以复用同一个 `NTFY_NOTIFY_TOPIC`，在 Cloudflare Quick Tunnel 地址变化后把新的 PocketCodex 链接发送到手机。

最小配置：

```dotenv
WATCH_TRANSPORT=ntfy
NTFY_NOTIFY_TOPIC=your-long-random-topic
NTFY_BASE=https://ntfy.sh
```

运行：

```bash
python3 start_remote_codex.py
```

Windows PowerShell 使用：

```powershell
python .\start_remote_codex.py
```

脚本会：

1. 等待 PocketCodex 本地 API 可用。
2. 创建 Quick Tunnel，并等待生成的公网地址返回成功状态。
3. 保存带 `#token=` 的完整地址到私有运行目录。
4. 向 ntfy 发送标题为 `PocketCodex 新链接` 的通知；点击通知或 `打开 PocketCodex` 按钮会打开该地址。
5. 仅在通知成功后更新 `last-notified-url.txt`，相同地址不会重复发送。

ntfy 失败是非致命错误：PocketCodex 和 cloudflared 会继续运行，错误类型记录在运行目录的 `notify-error.log`，其中不会写入完整链接、topic 或认证令牌。Quick Tunnel 仍然是临时入口；辅助脚本停止后链接会失效，再次启动时会生成并推送新链接。

## 3. 运行自检

```powershell
python .\watch_approve.py --doctor
```

自检会检查占位符、通知通道和网络连接，并发送测试通知。

## 4. 配置 Codex hooks

生成适合当前绝对路径的配置片段：

```powershell
python .\watch_approve.py --print-codex-config
```

将输出合并到 Codex hook 配置，也可以参考 [`examples/codex/hooks.example.json`](../examples/codex/hooks.example.json)。

修改 hook 后，在支持 hook 管理的 Codex 交互界面中检查并信任对应 hook。未信任或修改后未重新信任的 hook 可能被静默跳过。

## 5. 分别测试

```powershell
# 模拟 Codex 审批事件
'{"event_name":"PermissionRequest","tool_name":"Shell","tool_input":{"command":"git push"}}' | python .\watch_approve.py

# 模拟任务完成
'{"event_name":"Stop","last_assistant_message":"PocketCodex notification test"}' | python .\watch_done.py
```

PocketCodex 通过独立的桌面 app-server 运行任务。该连接收到的审批请求不会自动出现在另一个已打开的桌面窗口中；当前远程端不会自动批准未支持的敏感操作，因此不要把通知脚本当作唯一安全边界。

## 6. 与 PocketCodex 的关系

- `remote_codex_server.py` 负责远程桌面 thread 控制。
- `watch_done.py` 和 `watch_approve.py` 由 Codex/Claude Code hook 调用。
- `start_remote_codex.py` 可在 Windows、macOS 和 Linux 启动 Quick Tunnel，并通过现有 ntfy 配置发送已验证的新 URL。
- `start_remote_codex.ps1` 保留用于特定 Windows 环境；新安装优先使用跨平台 Python 辅助脚本。
- 不配置通知脚本时，PocketCodex 的桌面任务列表、新建、继续、图片上传和停止功能仍可使用。

## 安全提示

- ntfy topic、Pushcut key 和 PocketCodex token 都属于敏感信息。
- 不要在公开仓库、Issue、截图或日志中暴露这些值。
- 审批通知超时或网络异常时，应回退到 Codex 本机的正常审批机制。
- 不要把通知按钮视为替代 Codex sandbox 和本机权限控制的唯一防线。
