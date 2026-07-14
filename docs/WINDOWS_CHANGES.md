# Windows 分支修改清单

更新日期：2026-07-15
分支：`windows`
起点：`5e0d9cc`

## 一、产品定位

- 将原有 Apple Watch / 手机审批脚本扩展为 PocketCodex：通过手机浏览器远程查看、继续和新建本机 Codex CLI session。
- 保留 `watch_approve.py`、`watch_done.py` 及原有 hook 能力；远程控制与通知审批可以独立使用。
- 重写中英文 README，补充安装、连接、安全边界、故障排查和项目结构说明。

## 二、手机远程控制

- 新增基于 Python 标准库的本地 HTTP 服务，默认仅监听 `127.0.0.1:8765`。
- 自动读取 `~/.codex/sessions`，显示最近 30 个 session、项目目录、最近指令和完整回复。
- 支持通过 `codex exec resume` 继续现有 session。
- 支持选择允许的项目文件夹，通过 `codex exec` 新建 session。
- 新建 session 的文件夹选择器默认只开放当前用户的 `Desktop` 和 `Documents`，可通过 `REMOTE_CODEX_ROOTS` 配置额外根目录。
- 支持显示 queued、running、completed、failed、cancelled 等状态和运行耗时。
- 支持“停止并修改”：终止当前 Codex 进程，并把刚才的文字和图片恢复到输入区；该功能不会删除已经写入 session 的历史，也不会回滚已完成的文件修改。

## 三、移动端界面

- 新增适配 iPhone Safari 的深色移动端界面。
- 支持 session 选择、状态轮询、语音输入和后续指令发送。
- 支持全屏查看 Codex 完整回复，解决小窗口无法可靠滚动的问题。
- 支持新建 session 的文件夹浏览器和第一条指令输入。
- 支持最多 4 张 JPEG、PNG 或 WebP 图片上传、预览和删除。
- 浏览器会在上传前压缩图片；服务端再次验证 Base64、文件签名、数量和大小。
- 临时图片使用随机文件名写入 `.remote_uploads/`，任务结束、失败或取消时自动清理。

## 四、认证与安全边界

- 首次启动生成随机 `REMOTE_CODEX_TOKEN` 并写入已忽略的 `remote.env`。
- 手机首次从 URL fragment 读取 token，随后保存在当前站点的 `localStorage`，API 使用 `X-Remote-Codex-Token` 请求头。
- 使用常量时间比较校验 token，并对静态资源和 API 设置 `no-store`。
- 只允许继续本机 session 文件中存在的 UUID，不提供任意 shell 命令接口。
- 文件夹路径经过规范化并限制在白名单根目录内，阻止通过 `..` 或符号链接逃逸。
- PocketCodex 使用非交互 `codex exec`；手机/手表审批 hook 不能替代 Codex sandbox、本机权限和访问令牌。

## 五、Windows 远程连接

- 新增 `start_remote_codex.ps1`，用于后台启动 PocketCodex 和 Cloudflare Quick Tunnel。
- 检测 Cloudflare `Tunnel not found` 后自动重建临时通道。
- 地址变化时读取现有 `watch.env`，向 `NTFY_NOTIFY_TOPIC` 发送 `Codex Remote - NEW LINK`。
- 新链接通知正文直接显示完整地址，同时提供 `OPEN CODEX` 按钮和整条通知点击跳转。
- 新增 `-InstallWatchdog`，安装每 5 分钟运行一次、允许使用电池的当前用户计划任务 `RemoteCodexWatchdog`。
- 新增 `-RemoveWatchdog`，用于停用远程访问前删除巡检任务，防止通道被自动重新启动。
- 保留 Tailscale Serve 作为能够在手机和电脑安装 Tailscale 时的私有网络备选方案。

## 六、ntfy 与手表通知

- README 明确区分核心远程控制和可选通知层：PocketCodex 核心功能不强制依赖 ntfy。
- ntfy 可用于任务完成、失败、额度提醒、支持的审批请求，以及 Windows Quick Tunnel 新地址通知。
- Pushcut 继续作为 iPhone / Apple Watch 更完整交互通知的可选方案。
- 真实 `watch.env`、`remote.env`、topic、API key 和 token 均不进入 Git。

## 七、文档与配置

- `README.md`：中文完整使用手册。
- `README.en.md`：英文完整使用手册。
- `docs/ARCHITECTURE.md`：组件、数据流、信任边界和安全模型。
- `docs/NOTIFICATIONS.md`：ntfy、Pushcut、通知和审批 hook 配置。
- `REMOTE_CONTROL.md`：远程连接快速参考。
- `remote.env.example`：访问令牌和项目根目录配置示例。
- `.gitignore`：忽略 `.remote_uploads/`、`release/` 和已有敏感配置规则。

## 八、验证结果

- `python -m unittest discover -s tests -v`：74 项测试通过。
- `python -m py_compile remote_codex_server.py watch_approve.py watch_done.py`：通过。
- `node --check remote_web/app.js`：通过。
- PowerShell 语法解析：通过。
- `start_remote_codex.ps1 -InstallWatchdog`：在 Windows 上实际安装成功，计划任务允许使用电池。
- 图片上传、session 续接、新建 session、停止任务、Cloudflare 通道重建和 ntfy 新链接均完成过真实链路验证。

## 九、已知限制

- 这是 Codex CLI session companion，不是 Codex 桌面端或 TUI 的实时画面镜像。
- Cloudflare Quick Tunnel 是公网临时入口，地址可能被回收，完整 tokenized URL 应视为密码。
- 运行状态保存在服务内存中，服务重启后不会恢复旧 run 的实时状态。
- 停止任务不能撤回已写入 session 的消息，也不能回滚 Codex 已经执行的修改。
- 图片在服务进程异常退出时可能遗留在 `.remote_uploads/`，需要人工清理。
