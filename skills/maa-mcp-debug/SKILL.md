---
name: maa-mcp-debug
description: 通过 MaaMCP 发现和连接 Android 模拟器，运行 MaaFramework Pipeline 与 Custom Action，排查设备、资源、Agent、识图和停止问题。适用于 MaaFGO 实机验证与试炼挑战。
---

# MaaMCP 设备与执行

设备操作使用 MaaMCP 的 ADB 控制器。安装见 `../maafgo-hellish-trial/references/setup.md`；更多故障见 [troubleshooting.md](references/troubleshooting.md)。先读当前项目 `AGENTS.md`，工具参数以本次 tools/list 为准，不假设历史 schema、控制器 ID 或端口有效。

## 发现、连接、截图

1. `find_adb_device_list` 取得本次设备。只有一个可连接并立即截图；多个设备优先匹配用户明确目标与仍在列表中的已验证实例，探查只截图、不点击。工具明确要求用户选设备时遵守该要求；无法分辨时再问用户。
2. `connect_adb_device` 使用发现返回的完整设备名称；禁止自行拼接设备名或跳过对象注册。每次新会话重新发现和连接。
3. `screencap` 取得完整图并由 AI 实际看图，确认区服、活动与页面入口。没有用户运行授权时停在只读诊断。
4. MuMu 之外的模拟器也可按发现/截图验证；先推荐 MuMu 并保留用户选择，不自动迁移或关闭实例。

默认以横屏 16:9、短边 720 的控制器坐标工作。MaaMCP 1.3.0 已验证默认截图、OCR 与工具点击共用控制器坐标，不手工乘 1.5。使用裁图或改变截图尺寸时，按本次元数据的 `image_to_controller` 换算；新版本以实际 schema 与截图信息验证，不能把旧倍率写成常量。

## 从 Task 到有效 Pipeline

MaaFGO 的 Task/Option 属于界面协议，`run_pipeline` 只运行传入的 Pipeline。加载 `assets/tasks/地狱的试炼.json` 不会自动选择它的选项。

准备时逐项核对：

- 从 `assets/interface.json` 递归读取 `import`，找到目标 Task 与引用 Option。保留玩家已有文件；从当前文件取得真实节点名和字段，不把下列历史示例当 API。
- 选定日服，资源层为当前 interface 声明的 base→jp。识别图片/模型也必须可用，仅合并 JSON 不会合并图片资源；需要多层 Resource 的工具若只能接一个路径，可在本地临时 bundle 合成相同相对目录（base 先、jp 覆盖），同时保留 `agent`、共享 `assets/resource/model` 等当前工程依赖及正确的 interface 路径。运行前离线核对模板/模型路径，不能只复制 Pipeline。
- 依次合成资源包 Pipeline、Task `pipeline_override`、选中 Option/case 的 `pipeline_override`，递归处理 case 的子选项和默认值。输入占位符按 `pipeline_type` 替换为正确类型；列表通常替换、对象按当前协议合并，不能凭 Python `dict.update` 丢失已有 `attach` 或 `next`。遇到未知合并语义时查协议和客户端实现，并离线核对后再运行。
- 生成独立 effective/override 文件与 `selection.json`，不覆盖工程资源。为每次实际挑战保存入口、选项、方案路径和哈希。
- 比对关键字段：方案源、是否启用编辑器配置、高级策略来源、编队方式、助战条件、不允许找不到后随机回退、挑战次数、资源消耗选项。至少将第一轮次数设为1，由 AI 在每场结算后核对任务差异。

截至 2026-11 的示例中，“编队与逐回合方案”把 `chaldea_import_source` 同时传给战斗、编队及助战节点；“高级策略→使用编辑器/chaldea配置”令 `原生自动战斗-高级策略配置.attach.native_battle_strategy_source` 为 `editor`。历史试炼另有 `native_battle_trial_profile: hellish_202611`，必须与该期机制和方案 `maaTrialStrategy` 匹配。当前期数不同则重新适配，不强塞历史 profile。

## 运行与观察

优先复用现有 Pipeline；固定界面动作、点击/滑动坐标和有界循环放 Pipeline，Python 负责动态数据与业务判断。新增内联 OCR 时按项目约定给每个嵌套项唯一非空 `sub_name`，提交前运行项目静态检查。

`run_pipeline` 一次加载全部依赖，通用文件在前、所选层覆盖在后、最终临时 override 最后；确需覆盖时用工具提供的 `on_conflict="overwrite"`。`resource_path` 指向这次资源 bundle；Custom Action 需要 `start_agent=true`、可定位的 `interface.json` 和包含项目依赖的 Agent 解释器。不要直接裸跑 `agent/main.py`，它需要 Agent 连接参数。

准备好实际当前画面后再运行入口；工具不会替你恢复起始页。先做截图/单节点识别，再完整挑战。保存返回状态、关键 Custom Action 日志与最终截图，`succeeded` 仅证明任务返回成功，不证明任务条件全部满足。超时设为有界，长调用若宿主有后台执行能力应持续接收日志，单次外层等待不阻塞用户沟通过久。

**战斗内禁止使用画面冻结/像素相似作为放行条件**，包括 `pre_wait_freezes`、`post_wait_freezes`、`Context.wait_freezes`。技能后识别目标页，换人/退场后核对预期从者身份，不能仅凭主界面出现。等待的重复确认针对状态特征，不针对像素不动。

## 失败断点与清理

错人、误点、超时、未预期恢复分支：先停止会继续操作的任务，立即截图并保存失败节点、ROI、模板、得分、阈值、点击框和最后成功节点。不能等退回入口才解释原失败。保留现场后再做有根据的恢复；不要连续盲点或无限重试。

本次结束或异常时在 `finally` 调用 `stop_pipeline(controller_id)`，核对结果，再关闭自己启动的 stdio 服务/Agent。停止失败不能声称已清理，需确认本次进程归属后有界结束；不终止别的会话或模拟器。技能被中断后恢复工作也先检查有没有残留任务，不并行启动第二个点击者。
