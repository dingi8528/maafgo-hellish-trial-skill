# 来源与维护边界

本包由 dingi8528 的 MaaFGO 地狱的试炼实战工作流整理而来，包含可公开迁移的技能说明、标准库辅助程序与离线测试。

- 主技能、MCP 说明、安装文档及测试脚本按本仓库 AGPL-3.0-or-later 发布。
- `maa_mcp_bootstrap.py` 由维护者本地 MaaFGO 调试工具改编为可分发的启动包装；只在本进程中适配设备发现与停止接口，不修改用户已安装包。
- `skills/bilibili-video-analysis` 根据维护者本地同名视频技能及下载脚本整理，移除了主机固定路径，增加试炼攻略流程、测速与慢速停止。该随包版本同样按本仓库许可发布；它不捆绑平台视频。
- MaaFGO 工程：[dingi8528/MaaFgo_NEW](https://github.com/dingi8528/MaaFgo_NEW)，公开发行：[xlxyvergil/MaaFgo](https://github.com/xlxyvergil/MaaFgo)，遵守各自许可。技能只引用项目路径与行为，不复制游戏图片或完整运行时。
- MaaMCP：[MAA-AI/MaaMCP](https://github.com/MAA-AI/MaaMCP)；MaaFramework：[MaaXYZ/MaaFramework](https://github.com/MaaXYZ/MaaFramework)。由使用者从官方来源独立安装，依各自许可使用。
- Atlas Academy：从 [API 文档](https://api.atlasacademy.io/docs)查询区服数据，引用时记录数据源及时间；不随本包复制完整数据库。
- MuMu 与 B站为各自权利人所有。用户提供的游戏画面、账号信息、攻略媒体及其著作权不因使用本技能转让，不自动公开。

下载与调试记录留在使用者本地。本仓库不需要用户上传 Cookie、GitHub Token、模拟器账号或游戏账号密码。
