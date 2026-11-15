# 有画面版：连续窗口视频与 BB 播报

仅在用户选择“有画面版”后加载。本模式保持试炼策略、任务核对、MCP 控制和预算不变，增加本地只读展示；简化版不启动网页或共享服务。

## 画面接入方式

采用浏览器 `getDisplayMedia()` → `HTMLVideoElement.srcObject` 的连续视频流，默认请求30 FPS，可选择15 FPS省电。视频只在本页播放，不发送给 Python 后端、不录制、不额外创建 ADB 控制器；AI 继续按原 MaaMCP 截图和日志分析游戏，不将每一帧送给模型。

浏览器需要用户主动点击“选择游戏窗口”，再在系统弹窗中选择 MuMu 或实际模拟器窗口。AI 不能跳过这个选择、替用户永久授权或用约束静默锁定某个窗口。下次重新共享仍可能再次弹窗；请让用户完成这一步，等待时继续不依赖画面的准备工作。[浏览器共享规则](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getDisplayMedia)

优先使用支持窗口共享的桌面 Chrome/Edge，通过 `http://127.0.0.1:<端口>/` 打开；内嵌浏览器不支持时，在用户已有独立浏览器中打开同一地址。不要用 `file://` 直接打开 HTML。取消选择保留旧流；停止共享、系统结束共享或关闭页面会释放视频轨道。页面隐藏时暂停文字轮询与 BB 动画，保留用户仍在共享的视频轨道。

共享分辨率由选中窗口与浏览器决定，保留完整画面比例；目标30 FPS不等于实测30 FPS。网页用视频呈现回调测实际帧率，便于观察卡顿；不把画面停顿或黑屏解释成战斗失败。模拟器最小化、系统权限、GPU与窗口采集实现都可能影响表现。

## 启动本地服务

路径相对于主技能目录，`--session` 必须指向用户本轮输出目录，示例路径自行替换。只在前置检查允许的只读展示范围内启动，不能因此跳过游戏操作门禁。

```powershell
python scripts/trial_live.py init --session 'D:\Games\MaaFgo_NEW\output\hellish-trial\本期\live' --title '当期地狱的试炼'
python scripts/trial_live.py serve --session 'D:\Games\MaaFgo_NEW\output\hellish-trial\本期\live' --port 18767
```

服务输出实际 URL 与本次进程信息，状态记录在会话目录。使用宿主支持的后台执行方式启动；Windows 自行用 `Start-Process` 时加 `-WindowStyle Hidden`，保留 stdout/stderr 本地日志。默认只监听127.0.0.1；端口已占用就换可用端口并告诉用户，不杀别的服务。

页面和后端不连接、点击或控制模拟器；没有写接口、跨域公开接口或云端上传。用户选择共享窗口后，视频可立即播放，播报按事件更新，通常允许数秒延迟。后端暂时断开会明确标示，保留共享画面，不把网络故障当战斗失败。

## 提供真实的类实时播报

AI 在准备、实际入战、阶段变化、等待、失败与结算核对后，写一条小事件 JSON，再发布到同一会话。使用结构化文件，不用 shell 拼接带引号的播报文字：

```json
{
  "kind": "commentary",
  "turn": "T3",
  "phase": "当前已核实阶段",
  "title": "前辈，先补好条件再出手",
  "detail": "本次候选暂缓攻击宝具；实际动作与任务变化仍需继续核对。"
}
```

```powershell
python scripts/trial_live.py publish --session '本轮会话目录' --event-file '本地事件.json'
```

可让 BB 使用简短俏皮的文字，但不编造成功、下一手牌、伤害或技能已经生效。这个版本提供**文字播报与人物循环**，无需在线 TTS、角色语音模型或实时视频转码。

长 Pipeline 执行期间，可只读跟随**本次 Agent** 的诊断日志，自动播报明确可识别的回合、阶段和提交事件：

```powershell
python scripts/live_log_feed.py --session '本轮会话目录' --source '本项目\debug\recognition-本次PID.log' --interval 2 --max-seconds 3600
```

日志文件由当前 `agent/custom/mfaalog.py` 确定。使用启动记录/进程确认本次PID，不能自动挑整个目录中最新的日志而串入其他任务。默认从启动读取位置之后跟随；确需回看已有本轮片段时用 `--from-start`，只补最多末64KiB，须避免把旧事件当新发生。自动模板只是事实摘要，未知行不播；没有匹配日志时由 AI 发布确认过的事件，不做无限重试。

## 任务面板与证据

- 用 `kind:"missions"` 和 `missions` 数组初始化当期真实任务列表；元素为 `id / label / detail / status / evidence`。数量动态生成，尚未读取时显示未知，不预置15项全满。
- `pending`：待核对；`observed`：本场观察到候选事件；`confirmed`：已有可定位的核对依据（`evidence` 必填）；`failed`：该条件本场未满足。
- 以 `kind:"mission", "mission":{...}` 更新单项。仅观察到的事件不计入确认数。日志出现胜利也不会自动勾完所有任务。
- `baseline_count` 与 `game_count` 单独记录游戏任务页前后状态；本场核对数与账号原有完成数分开。账号起点已满时不得借此宣称本轮新增任务。
- 任务确认应来自实际任务页差异，或在起点已满无法新增验证时写明完整事件依据及限制。面板不能替代最终游戏任务页核对。

支持的事件类型是 `status / commentary / missions / mission / warning / finish`；状态为 `waiting / ready / running / paused / finished / failed / stopped`。无需向网页写入游戏密码、Cookie或令牌；文本用安全 DOM 渲染。

## 素材与性能选择

网页、背景SVG、透明 BB 循环和静态人物图都随 `assets/live/` 提供，运行时无需联网下载、Node.js 构建或 FFmpeg。BB 循环约3MB；动画可关闭，改用静态图。需要更省资源时选择15 FPS和较慢播报刷新；简化版则不运行展示页。

重建人物素材时，可用 `scripts/prepare_bb_loop.py --input '已有透明视频.webm' --output-dir '新输出目录' --ready` 复制现成循环并生成透明静态图。未成循环的透明素材可去掉 `--ready`，必要时显式使用 `--ping-pong`；已经正倒放的素材不要再次反转。重建才需要 FFmpeg；其来源记录见 `assets/live/SOURCES.md`。

性能评估口径和复测方法见 [性能与验证记录](live-performance.md)。此前低频JPEG的数字不适用于本连续视频方案。

## 结束与恢复

切换回简化版前先点“停止共享”或关闭观察页，确认视频轨道释放，再停止本轮日志播报进程及展示服务。**单独停止后端不会停止浏览器仍在运行的窗口共享。** 只终止有本轮PID与会话路径证据的进程，不关闭模拟器或其他任务。

页面只读，没有开始战斗按钮。展示失败时保留游戏现场，告知可以继续用简化版；不为了恢复展示重复执行游戏动作。
