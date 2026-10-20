# 云端翻唱短样：本机操作

当前提供独立的 `cover` 命令。它用于准备片段、调用 Kits 分离/转换并生成本机混音；不接群内点歌，不向微信发送，不调用 DeepSeek。真实账号和歌曲还未提供，尚未完成真实云端试听验收。

## 1. 账号和工具

1. 打开 [Kits 官方 API 页面](https://www.kits.ai/api)，注册/登录后进入 API 页面。
2. 确认账号具有 API 权限，使用 `New Token` 创建密钥；如果页面要求申请接入，先申请。网页功能可用不等于 API 权限已开通，确认前无需为此购买套餐。
3. 在项目根目录运行：

```powershell
uv sync --extra native --extra media
if (-not (Test-Path .env.cover)) { Copy-Item config/cover.env.example .env.cover }
notepad .env.cover
```

只把翻唱密钥填入 `.env.cover` 中 `WECHAT_AI_BRIDGE_KITS_API_KEY=` 后面。不要发到聊天、截图或提交。此配置独立于聊天 `.env`，不需要替换 DeepSeek 密钥；已存在的进程环境变量优先于文件。

`media` 可选依赖包含便携 FFmpeg；不会修改系统 PATH。也可在配置中指定已有 FFmpeg 的完整路径。以下检查仅检查本机配置和工具，不请求服务、不证明 API 权限或音色可用：

```powershell
uv run --extra native --extra media wechat-ai-bridge cover check
```

## 2. 查询声音

```powershell
uv run --extra native --extra media wechat-ai-bridge cover voices --page 1
```

这是带认证的只读声音目录查询，不创建生成任务。依据 `page/lastPage` 翻页，从标题和标签中选择现成女声，并记录其 `id`。`isUsable` 必须为 `true`；旧格式缺失该字段时保守视为不可用。命令不自动替用户更换声音。官方快速示例的 Female LoFi ID 为 `1014961`，但实际可用性以账号查询为准，不能仅照抄编号。

本机 `WECHAT_AI_BRIDGE_KITS_FEMALE_VOICE_IDS` 为管理员核对后的女声编号名单，默认仅含官方示例的 `1014961`。选择其他现成女声时先核对目录再加入，多个编号用逗号分隔。付费提交前同时检查名单、可用性以及标题/标签中的 Female 或女声标识；未获明确识别的声音拒绝提交。

## 3. 仅准备本地片段

准备本机 WAV、MP3 或 FLAC 音频，明确歌曲名、歌手与起始时间。例如下面从 30 秒开始取 20 秒，`--voice-id` 应替换为选中的可用声音编号：

```powershell
uv run --extra native --extra media wechat-ai-bridge cover prepare --input "D:\Music\sample.mp3" --song "试听歌曲" --artist "演唱者" --start 30 --duration 20 --voice-id 1014961 --session trial-01
```

起始时间必须显式给出；片段长度为 1～30 秒，默认 20 秒。可用 `--pitch-shift` 在 -24～24 半音内调整，默认 0。对男声原曲转换女声时，先保持默认试听，再根据结果调整；改变参数要准备新 session，不修改已提交任务。

`prepare` 完全离线，输出 `data/runtime/cover/trial-01/clip.wav` 和 `manifest.json`。先播放 `clip.wav`，确认片段含有人声且选择正确，再显式执行下一步。已存在的 session 不覆盖，失败准备可换一个名称重做。

已有同步的人声与伴奏时，用 `--vocal "D:\Music\vocal.wav" --backing "D:\Music\backing.wav"` 替换 `--input ...`，其余参数保持不变。两轨从相同起点裁剪，云端跳过分离，只上传人声做转换；伴奏留在本机。

## 4. 执行与恢复

以下命令会把已准备的片段上传 Kits，并可能消耗服务用量：

```powershell
uv run --extra native --extra media wechat-ai-bridge cover run --session trial-01
```

流程为：校验片段哈希和声音可用性 → 分离人声/伴奏 → 转换女声 → 本机混音。每个阶段先保存提交状态，获得任务编号后立即记录。轮询每 5 秒一次，每阶段最多 60 次；`--max-polls` 可设为 1～60。服务请求有独立超时。生成期间不阻塞原来的聊天进程。

```powershell
uv run --extra native --extra media wechat-ai-bridge cover status --session trial-01
```

- 轮询超时、下载失败后，再次对同一 session 执行 `run`，查询已有任务，使用新签名 URL 获取结果，不重新提交已知阶段。
- `submitting` 表示曾开始提交但没有可靠保存任务编号。此时不自动再提交；先到服务后台核对实际任务和用量，再进行人工处理。不要直接反复创建新 session。
- 服务端失败/取消不会自动重提。任务内容和参数需要修改时，先核对已存在任务，再显式建立新 session。
- 不要编辑任务 manifest；剪辑和已确认的中间音频使用哈希检查，变动后拒绝继续。
- 同一个 session 只允许一个运行实例，进程退出后锁由操作系统释放。

成功时状态为 `complete`，`output` 指向 `data/runtime/cover/trial-01/cover.wav`。本机检查包括长度、静音和峰值，不能代替人耳判断中文咬字、旋律准确性和音色自然度。最终听感由用户确认。

## 5. 下载与数据边界

API 固定连接官方 `https://arpeggi.io/api/kits/v1`。结果下载是另一条无 Bearer 的请求，限定 HTTPS、精确主机名单、文件大小，禁止自动重定向；本机解码禁止网络协议和播放列表格式。上传最大 10 MiB，结果下载最大 20 MiB，短样均为受限时长。

默认下载主机来自官方公开示例，真实账号输出可能采用其他存储主机。遇到 `kits_download_url_invalid`，先在服务后台核对该任务的结果域名，再将精确域名加入本机 `WECHAT_AI_BRIDGE_KITS_DOWNLOAD_HOSTS`，不要添加通配符或把带签名的 URL 发到聊天中。修改配置后重新运行同一 session，仅查询已有任务并重新下载。

密钥不出现在命令参数、JSON 输出或 manifest。签名 URL 只保留在请求内存中，不保存进 manifest。音频和任务元数据均位于 Git 忽略的 `data/runtime/cover/`；任务记录不包含原歌曲完整路径。保留这些记录用于恢复及核对用量，删除记录会失去防重复提交依据。

## 6. 验证范围

离线测试用虚构服务响应验证状态机、HTTP 边界与错误处理，用本地生成的音调验证真实裁剪和混音。它们不证明 Kits 账号已可用、云端歌曲效果良好或微信媒体发送成功。

```powershell
uv run --extra native --extra media python -m unittest discover -s tests -q
git diff --check
```

后续顺序：真实短样试听通过 → 确定音色 → 微信音频发送与回执 → 群内点歌任务。表情包按独立方案推进。

## 官方依据

- [API 能力和获取密钥](https://www.kits.ai/api)
- [声音转换请求](https://docs.kits.ai/api-reference/api-endpoints/voice-conversion-api/create-new-voice-conversion-job)
- [人声分离请求](https://docs.kits.ai/api-reference/api-endpoints/vocal-separations-api/create-new-vocal-separation-job)
- [推理任务与音频结果](https://docs.kits.ai/api-reference/types-limits/inference-job)

以上接口契约核对日期：2026-10-20。账号权限和用量以服务后台为准。
