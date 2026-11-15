# 上游 Maa 技能与 MaaFGO 定制补充

## 来源与职责

通用 Maa 技能直接从第三方上游 [Everything-Maa](https://github.com/KhazixW2/Everything-Maa) 获取，本仓库不分发其 `SKILL.md`、引用资料或脚本副本，也不提供同名替代技能。MaaMCP 是独立的 MCP 服务，安装和工具契约以 [MaaMCP 上游](https://github.com/MaaXYZ/MaaMCP)及本次 tools/list 为准，不把服务仓库和技能仓库混为一谈。

按需读取真实上游入口；已安装时读完整技能目录，未安装时从仓库读取入口及其所引用资料，不只截取一段正文后猜操作：

| 场景 | 上游技能 |
|---|---|
| 设备、资源、识别节点与 Custom Action 测试 | [maa-pipeline-testing](https://github.com/KhazixW2/Everything-Maa/blob/main/skills/maa-pipeline-testing/SKILL.md) |
| 任务完成条件、状态模型与恢复流程 | [maa-workflow-build](https://github.com/KhazixW2/Everything-Maa/blob/main/skills/maa-workflow-build/SKILL.md) |
| Task/Option 与 Pipeline 覆盖接线 | [maa-pipeline-option](https://github.com/KhazixW2/Everything-Maa/blob/main/skills/maa-pipeline-option/SKILL.md) |
| Interface 协议与资源声明 | [maa-interface-guide](https://github.com/KhazixW2/Everything-Maa/blob/main/skills/maa-interface-guide/SKILL.md) |
| 已失败且责任方仍不明的只读诊断 | [maa-diagnose](https://github.com/KhazixW2/Everything-Maa/blob/main/skills/maa-diagnose/SKILL.md) |

完整安装、版本检查和卸载规则读 [Everything-Maa README](https://github.com/KhazixW2/Everything-Maa/blob/main/README.md)。它是第三方社区技能集，不是本仓库或 MaaFramework 官方发行包。先检查用户已有技能来源和版本；更新时保留用户修改。

### 安装选择

在用户选定的项目目录运行，执行前说明安装范围、检查已有配置，按本技能的测速规则处理 npm 下载：

```powershell
# 已有可用 MaaMCP：先预览，仅安装上游技能，不重配 MCP
npx everything-maa@latest install --target codex --profile skills-only --dry-run
npx everything-maa@latest install --target codex --profile skills-only
```

需要同时配置 MaaMCP 时，可选择上游 `core` profile；它会安装整套 Maa 技能并配置 MCP，不只是增加一个技能。Claude 等宿主的 target、用户级/项目级范围和 uv/Node.js 依赖按上游当前说明选择，不凭本仓库示例覆盖宿主全局配置。已经有可用 MCP 时不重复安装。不能执行 npm 的用户可从上游 `Code → Download ZIP` 获取完整技能目录及依赖；公开下载不要求 GitHub 账号。

本仓库只附下面的 **MaaFGO 定制知识与启动兼容工具**，不复制上游通用操作手册。若上游技能无法取得，报告缺失入口及原因；不要偷偷退回旧包中的 `maa-mcp-debug` 副本。

## 试炼授权与动态画面

上游用于测试的保守操作模板不改变当前用户的明确要求：用户已授权试炼代打时，可以在该预算内开始战斗、编队、技能、出卡及结算；只要求诊断或局部测试时，按用户停止点执行。安装/阅读技能不等于授权游戏操作。

MaaFGO 战斗包含持续动作与特效。不得用 `pre_wait_freezes`、`post_wait_freezes`、`Context.wait_freezes` 或等价像素冻结检测放行技能、换人、退场和候补；局部 ROI 也不例外。按项目 `AGENTS.md` 识别具体页面、技能按钮及预期从者身份。确认的是目标状态持续成立，不是画面相似；主界面单点出现不足以证明换人完成。

## MaaFGO Task 到有效 Pipeline

`run_pipeline` 不解析 MXU Task/Option。接入“地狱的试炼”前，结合上游选项/Interface 技能核对以下项目特有要求：

- 从本次 `assets/interface.json` 递归读取 `import`，查当前 Task、选项和资源服别，不用历史节点名猜配置。
- 日服按 interface 声明的 base→jp 资源层加载。工具只能接受单一路径时，在临时 bundle 合成同名相对目录，base 先、jp 覆盖；保留 Agent、共享 `assets/resource/model` 及正确 interface 路径。只复制 JSON 不会带上模板和模型。
- 合成资源 Pipeline、Task 覆盖、所选 Option/case 覆盖与子选项；输入按 `pipeline_type` 替换。对象和列表合并按当前协议，不能用浅 `dict.update` 丢失 `attach` 或 `next`。
- 保存独立的 effective/override 文件、`selection.json`、方案路径和哈希，不改工程原始资源。Custom Action 使用可定位的 interface 与装有项目依赖的 Agent 解释器；不要裸跑 `agent/main.py`。
- 入战前检查方案源同时传到战斗、编队和助战，确认高级策略来源、编队方式、助战条件、次数及资源消耗项都生效。助战不得因找不到而自动降为随机首位；第一轮设1场，结算后读任务差异。

2026-11 历史示例中，`chaldea_import_source` 同时用于多处节点；“高级策略→使用编辑器/chaldea配置”使 `原生自动战斗-高级策略配置.attach.native_battle_strategy_source` 为 `editor`。`hellish_202611` 只能用于已核实匹配该期机制的方案；新期数重新适配，不能只改名字。

## 项目实战排错补充

通用设备发现、坐标和工具协议读上游技能。下面只记录 MaaFGO 实战中需要额外核对的点：

| 现象 | 定制核对与处理 |
|---|---|
| MuMu 已运行却发现为空 | 从当前安装位置取得实例的 adb.exe，必要时使用随包 `scripts/maa_mcp_bootstrap.py --adb-path ...`；仍通过 MaaMCP 正式发现、注册、连接，不猜固定端口 |
| `adb.log` 不可写或 host daemon 缺失 | 兼容包装只在本地 daemon 未运行时用私有 TEMP 启动；已有则复用，不删除用户日志或 kill 全部 ADB |
| Agent 导入失败/Custom Action 未注册 | MCP/Agent 解释器、依赖和 MaaFramework 版本分别核对，临时 bundle 保留正确相对路径；不要从作者电脑复制 site-packages |
| 历史版本缺 `Tasker.stop()` | 随包包装仅在缺接口时进程内适配 `post_stop().wait()`；优先使用上游已修复版本，不改已安装包 |
| Result 无法序列化但画面似已变化 | 先截图核对执行是否实际发生，再研究兼容性；不要因缺响应再放一次技能 |
| 无茶壶界面 | 某些活动正常没有该页，复用工程“无入口则跳过”分支，不为测试消耗茶壶 |
| 换人后身份/技能不符 | 主界面出现不足以证明完成；复核预期从者进入/离开与绑定变化，保留现场后处理 |
| 仓库或助战错识别 | 使用该页面专用模板和当前 ROI；串行查找、滑动、重新识别，不并行点击旧框，不为继续而降低身份阈值 |

MuMu 官方 ADB 设置见 [官方说明](https://mumu.163.com/help/20240807/40912_1073151.html)，版本差异按当前界面核对，不照抄历史端口。

异常先保存现场截图与最近日志，再按授权恢复；最终报告需要同时核对游戏任务状态、关键 Custom Action 事件和最终页面。停止结果与本次进程归属也要复核，不能用 `succeeded` 单独证明任务全满或后台已清理。
