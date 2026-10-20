# 云端翻唱短样实施计划

> 执行方式：当前会话按模块实现并审查；不自动提交。用户已批准设计方案中的阶段 A。

**目标：** 管理员在本机显式准备歌曲片段、选择 Kits 女声，执行一次分离与音色转换，得到本机可试听混音。真实账号和音频尚未提供。

**架构：** 独立 `media` 模块负责 Kits HTTP、音频工具和短样流程；`cover` 命令单独载入配置，不创建聊天状态库，不调用 DeepSeek 或微信。任务记录先写磁盘再发请求，响应不明确时保留状态，显式恢复只查询已知任务，不盲目重提。

**技术：** Python 3.12 标准库 HTTP/JSON/subprocess、现有 dotenv、FFmpeg 外部工具、unittest。`media` 可选依赖 `imageio-ffmpeg` 提供便携 FFmpeg，无额外云端服务 SDK。

## 文件职责

- `media/kits.py`：固定官方 API、Bearer、multipart、声音列表、任务校验、受限音频下载；错误只含固定代码。
- `media/audio.py`：本地工具检测、受限解码裁剪、长度/音量检查、混音；参数数组调用，无 shell。
- `media/cover.py`：独立配置与命令、短样 manifest、单实例、状态机和有界轮询。
- `cli.py`：分发 cover 子命令，保持旧命令兼容。
- `tests/test_kits.py`、`tests/test_cover_audio.py`、`tests/test_cover_sample.py`：分别验证传输边界、真实短音频处理、任务恢复和命令隔离。
- `config/cover.env.example`、`docs/cloud-cover-sample.md`：独立 `.env.cover` 安全配置与可运行操作步骤。

## 任务与验证

- [x] 1. 写服务适配测试：固定路由、multipart 字段、无密钥拒绝、超限/畸形响应、POST 无自动重试、状态不匹配、下载无凭据且限制主机和长度。先运行确认功能缺失，再实现。
- [x] 2. 写音频测试：工具缺失、非有限时间、片段越界、媒体协议限制、真实 WAV 裁剪与混音、静音/峰值检查。先红后绿。安装工具仅采用用户已批准的本机短样所需受控依赖。
- [x] 3. 写流程测试：准备不上传、重复目录拒绝、提交前落盘、任务 ID 即时保存、失败不重提、恢复只查已知任务、有界轮询、成功仅输出本机路径。用假 provider 和可控时钟验证。
- [x] 4. 接入 `cover check/voices/prepare/run/status`：prepare 先产生待核对片段；run 只对已准备目录执行；显式恢复同目录不重新计费提交。未配置 Kits 时不影响聊天启动。
- [x] 5. 文档：列出 API 权限、样本歌曲与开始时间；配置密钥只在本机，音频和 manifest 都在忽略目录；真实生成与微信群发送明确分开。
- [x] 6. 规格审查与质量审查，修复后运行 `uv run --extra native --extra media python -m unittest discover -s tests -q`、`git diff --check`，并运行无密钥 `cover check` 和本地合成短音频演练。

实际接口以官方文档为准：`https://arpeggi.io/api/kits/v1/voice-models`、`vocal-separations`、`voice-conversions`；分离使用 `inputFile`，转换使用 `soundFile` 和 `voiceModelId`。任务状态 `running/success/error/cancelled`，结果只持久化任务编号，不保存签名下载 URL 或密钥。

交付本机短样工具后，真实试听仍需用户提供账号权限、密钥和歌曲。表情包、群内点歌队列及微信媒体发送不在本次实现范围。

## 本轮验证结果（2026-10-20）

- 完整回归 150 项通过，新文件格式及 `git diff --check` 通过。
- 服务适配通过虚构响应验证；真实 FFmpeg 用合成音调完成裁剪、混音与 CLI 准备/状态演练，未上传歌曲或调用付费任务。
- 规格审查修复了女声范围校验和预分离音轨入口；质量审查修复了混音非原子发布问题。恢复回归确认发布中断不会重提分离或转换任务。
- 本机检查显示 FFmpeg 就绪，翻唱密钥尚未填写；独立 `.env.cover` 和运行产物受 Git 忽略。原聊天进程仍在运行，标准错误日志为空。
- 真实 API 权限、声音可用性及歌曲听感尚未验证，不标记真实云端翻唱完成。代码保留在 `codex/cloud-cover-sample`，未自动提交。
