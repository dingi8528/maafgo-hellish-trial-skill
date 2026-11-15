# MaaFGO 地狱的试炼 Skill

让支持识图和本地 MCP 的 AI，研究 FGO **当期**地狱的试炼，编排队伍与战斗计划，通过模拟器逐场验证任务，并保存可编辑策略。

这是一套给 AI 的工作技能，不是单独的游戏外挂程序或固定通关宏。每期机制、账号练度和发牌不同，不能保证任意账号十场内完成。使用者应了解并自行遵守游戏与所在平台的规则。

## 包含什么

| 目录 | 用途 |
|---|---|
| [maafgo-hellish-trial](skills/maafgo-hellish-trial/SKILL.md) | 环境检查→识别当期与任务→找攻略→规划→MCP挑战→任务核对→策略保存 |
| [bilibili-video-analysis](skills/bilibili-video-analysis/SKILL.md) | 简介、分P、公开视频下载、测速/ETA、完整解码校验、抽帧与攻略时间轴 |

**上游依赖直接引用，不随包复制：** 通用 Maa 技能从第三方 [Everything-Maa](https://github.com/KhazixW2/Everything-Maa) 获取，例如 [Pipeline 测试](https://github.com/KhazixW2/Everything-Maa/blob/main/skills/maa-pipeline-testing/SKILL.md)、[选项接线](https://github.com/KhazixW2/Everything-Maa/blob/main/skills/maa-pipeline-option/SKILL.md)与诊断；MaaMCP 服务从 [MaaMCP 上游](https://github.com/MaaXYZ/MaaMCP)安装。本仓库仅补充 [MaaFGO 接入与实战经验](skills/maafgo-hellish-trial/references/mcp-customization.md)，不维护一份同名 MCP 技能。

补充了实战中容易遗漏的事项：任务基线、跨场补条件、冠位/羁绊 NP50 实际设置、宝具攻击与辅助效果的区别、技能真实冷却、助战 MIX、随机等待、中途恢复、最终整合版复跑的证据标准。历史 2026-10/11 方案只作为案例，不自动视为现在的当期方案。

## 零基础安装

**无需 GitHub 账号。** 在本仓库点绿色 **Code → Download ZIP**，完整解压，把 `skills` 下两个目录复制到 AI 宿主的技能目录。例如 Codex 通常是：

```text
用户目录/.codex/skills/
  maafgo-hellish-trial/SKILL.md
  bilibili-video-analysis/SKILL.md
```

两个目录放在同一级，以便相对路径读取；已有同名技能时先比较和备份，不直接覆盖个人修改。再按[上游安装选择](skills/maafgo-hellish-trial/references/mcp-customization.md#安装选择)获取 Maa 通用技能；已有可用 MCP 时可选 `skills-only`，不重复重配 MCP。支持技能导入的其他 AI 宿主按自身界面操作；没有技能加载器时可让 AI 直接读取主 `SKILL.md`。重新加载技能后输入：

> 用 $maafgo-hellish-trial 帮我完成日服当前的地狱的试炼。先检查安装环境和版本，询问攻略偏好；每场核对任务进度，最后保存可编辑方案。默认本轮最多入战10场，只用自然AP，不用苹果、圣晶石、令咒和茶壶。

只想先检查环境、做方案或测试到某个阶段时，直接说明停止点；安装技能本身不会启动游戏战斗。

从旧包升级：此前随包的 `maa-mcp-debug` 已移除。仅当确认本机副本由本仓库安装且未自行修改时，才在备份后移除该旧副本；不要删除其他来源或自行维护的同名技能。

## 运行需要什么

- 能实际查看图片的 AI 模型，以及能访问本地文件、执行命令和调用 stdio MCP 的宿主。只支持文字或只返回截图路径时先解决图像输入，不能盲点代打。
- 已安装且能进入对应区服的模拟器。推荐 [MuMu](https://mumu.163.com/)，已有雷电等其他模拟器可由用户选择继续验证。
- 上游 [Everything-Maa](https://github.com/KhazixW2/Everything-Maa) 技能、[MaaMCP](https://github.com/MaaXYZ/MaaMCP)、MaaFramework 与 MaaFGO。开发源码：[dingi8528/MaaFgo_NEW](https://github.com/dingi8528/MaaFgo_NEW)；桌面包：[xlxyvergil/MaaFgo Releases](https://github.com/xlxyvergil/MaaFgo/releases)。源码 ZIP 与桌面包不同，完整步骤见[安装与更新](skills/maafgo-hellish-trial/references/setup.md)。
- 视频分析使用 Python，抽帧联系表用 Pillow，视频解码用 FFmpeg。可在隔离环境安装 `Pillow imageio-ffmpeg`；脚本不安装全局依赖。公开视频访问受限时可使用用户有权提供的本地视频，不捆绑登录 Cookie 或第三方解析服务。

每次联网检查版本和下载速度，先报告估计时间。下载很慢时由用户选择继续、手动下载或使用自己信任的方式；本包不会自动改全局代理或切不明镜像。

战前先问用户是否有攻略；没有时可授权 AI 优先在 [UP 78011410](https://space.bilibili.com/78011410)、[UP 2113516](https://space.bilibili.com/2113516) 中检索，核对标题、当期、发布时间和实际内容。无法下载/观看时会注明限制，不把封面和标题当作已看完的视频。

## 交付与隐私

挑战过程与媒体默认保存在使用者项目的 `output/hellish-trial/<期名或日期>/`，编队写入 `config/Battle/`，不写进技能目录。输出包含任务前后状态、逐场记录、策略限制、可重载 JSON 及复测范围。账号原已全满、局部实测、原战热修续跑和最终文件完整复跑分别说明。

默认只用自然 AP；购买、强化、令咒和额外资源需要用户指定。修改策略只限试炼；不会自行改其他任务默认战斗行为，也不会未经要求把截图、账号资料或策略推送到公开仓库。

## 本包验证

在仓库根目录执行：

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 skills/bilibili-video-analysis/scripts/test_bilibili.py -v
```

测试使用本地 HTTP 服务、临时 Git 仓库和合成短媒体，验证测速边界、版本未知状态、下载与完整性检查；不消耗游戏资源。FFmpeg/Pillow 不可用时相关媒体测试会跳过，应按实际测试输出报告。脚本测试通过不证明任意新一期已经实机适配。

## 许可与来源

本包内容以 [AGPL-3.0-or-later](LICENSE) 发布，来源及依赖边界见 [NOTICE.md](NOTICE.md)。Everything-Maa 等外部技能由其上游许可管理，不因引用而改用本仓库许可。不包含上游 Maa 技能副本、MaaFGO 游戏资源、MaaMCP 二进制、B站攻略视频、账号数据或本机战斗截图。策略开发仍以所用 MaaFGO 版本的当前源码和 `AGENTS.md` 为准。
