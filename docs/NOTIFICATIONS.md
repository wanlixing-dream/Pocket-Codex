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

至少填写通知通道所需的值：

```dotenv
NTFY_NOTIFY_TOPIC=your-long-random-topic

# 使用 Pushcut 时填写
PUSHCUT_KEY=your-pushcut-api-key
PUSHCUT_NOTIF=your-notification-name
```

真实 `watch.env` 已被 `.gitignore` 排除，不应提交。

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

修改 hook 后，在 Codex TUI 中运行 `/hooks`，检查并信任对应 hook。未信任或修改后未重新信任的 hook 可能被静默跳过。

## 5. 分别测试

```powershell
# 模拟 Codex 审批事件
'{"event_name":"PermissionRequest","tool_name":"Shell","tool_input":{"command":"git push"}}' | python .\watch_approve.py

# 模拟任务完成
'{"event_name":"Stop","last_assistant_message":"PocketCodex notification test"}' | python .\watch_done.py
```

真实运行时，Codex 的交互式 TUI 与 `codex exec` 的 hook 行为可能不同。PocketCodex 远程任务使用 `codex exec`，不要把手机远程审批当作所有非交互任务都必然触发的安全边界。

## 6. 与 PocketCodex 的关系

- `remote_codex_server.py` 负责远程 session 控制。
- `watch_done.py` 和 `watch_approve.py` 由 Codex/Claude Code hook 调用。
- `start_remote_codex.ps1` 可在特定 Windows 环境中启动 Quick Tunnel，并通过现有 ntfy 配置发送新 URL。
- 不配置通知脚本时，PocketCodex 的 session 列表、新建、继续、图片上传和停止功能仍可使用。

## 安全提示

- ntfy topic、Pushcut key 和 PocketCodex token 都属于敏感信息。
- 不要在公开仓库、Issue、截图或日志中暴露这些值。
- 审批通知超时或网络异常时，应回退到 Codex 本机的正常审批机制。
- 不要把通知按钮视为替代 Codex sandbox 和本机权限控制的唯一防线。

