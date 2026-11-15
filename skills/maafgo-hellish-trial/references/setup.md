# 安装、更新与下载诊断

面向 Windows + Android 模拟器。默认用户已有模拟器；推荐 MuMu，但不把更换模拟器当作前提。其他系统需先验证 MaaMCP 与该宿主的设备后端，不承诺本包实机覆盖。

## 1. AI 宿主与识图检查

宿主需要：本地文件读写、运行命令、连接 stdio MCP、访问网页，以及将 MCP 截图作为**图片内容**交给当前模型。只有云端聊天、无法连用户电脑时，不声称已经操控模拟器。

从 MCP 获取完整截图，打开图片并描述页面上的具体标题与按钮。若只收到路径，尝试宿主图片查看能力；OCR 结果不能替代本项视觉能力确认。模型不支持图像输入时，请用户切换支持识图的模型。无需把模型名写死在技能中。

## 2. 下载前先检测现有环境和新版本

在主技能目录运行（`--project` 用用户实际目录）。**用 MaaMCP 真正启动时的同一个 Python 解释器执行检查**；普通 `python` 的缺包结果不代表另一个虚拟环境也缺包。尚未安装 MCP 时才先用现有 Python 盘点：

```powershell
python scripts/check_environment.py --project 'D:\Games\MaaFgo_NEW' --online
```

分别核对来源：

| 对象 | 权威来源 | 如何判定 |
|---|---|---|
| 本技能包 | https://github.com/dingi8528/maafgo-hellish-trial-skill | 当前默认分支提交；本包根 `VERSION` 是技能版本 |
| 策略开发工程 | https://github.com/dingi8528/MaaFgo_NEW | API 返回默认分支的 SHA，与本地 HEAD 对比；有本地改动先保留 |
| 桌面发行包 | https://github.com/xlxyvergil/MaaFgo/releases | 最新非预发布 Release，检查附件、平台与发行说明 |
| MaaMCP | https://pypi.org/project/maa-mcp/ | PyPI 当前版本与 MCP 所在虚拟环境版本 |
| MaaFramework Python | https://pypi.org/project/maafw/ | 分别检查 MCP 和 Agent 解释器版本，避免混用 |

开发仓库可能没有 Release；这不等于发行仓库没有更新。ZIP 没有 `.git` 时，仅靠目录名或源码 `interface.json.version` 无法确认提交最新；重下当前默认分支到新目录，或保留下载时间与 API SHA，不能伪称已核实旧 ZIP。在线检查被限流、403/404、网络失败时标记“无法确认”，保留原因并给用户链接，不把失败当“已是最新”。

技能本身也要检查更新：Git 安装时先只读取得远端默认分支 SHA，与本地 HEAD 比较；本地有修改时不自动覆盖。ZIP 安装时查询仓库默认分支 API `https://api.github.com/repos/dingi8528/maafgo-hellish-trial-skill/commits/main`，记录下载时间及 SHA，新旧无法对应时标为未知，必要时另目录下载当前版本。`check_environment.py` 检查工程和依赖，不负责证明技能副本最新。

升级要在无任务运行时进行，先备份 `config/Battle`、个人库及用户配置。Git 工作区有改动时不自动 reset/覆盖；优先隔离 checkout。新依赖版本不等于兼容版本，先检查发行说明并用一张截图和最小识别验证，再开始挑战。

## 3. 没有 GitHub 账号也可以使用

公开仓库浏览、`Code → Download ZIP` 和公开 Release 附件下载均不要求注册。无需 Fork、创建仓库或配置令牌。

1. 打开[技能包仓库](https://github.com/dingi8528/maafgo-hellish-trial-skill)，点绿色 **Code → Download ZIP**，完整解压。
2. 将解压目录 `skills/` 下的三个技能文件夹，复制到当前 AI 宿主的技能目录。Codex 通常为用户目录下 `.codex/skills/`；其他宿主按其技能导入界面选择包含 `SKILL.md` 的目录。不要把 ZIP 本身当技能。
3. 重新加载技能或重启宿主，输入 `$maafgo-hellish-trial`；如果宿主不识别 `$`，直接让它读取主技能的 `SKILL.md`，相邻技能也可按相对路径读取。

若熟悉 Git，可以 `git clone https://github.com/dingi8528/maafgo-hellish-trial-skill.git`；下载公开代码无需 GitHub 账号。更新技能前比较差异，避免覆盖用户自行编辑的版本；只替换本技能包的目录，不覆盖宿主全局设置。

## 4. 测速、ETA 与慢速选择

对准备下载的**具体附件 URL**运行：

```powershell
python scripts/network_probe.py 'https://实际官方附件地址' --bytes 2097152 --seconds 5
```

先从 GitHub Release asset 的 `size`、PyPI 文件清单或视频 metadata 获取大小；必要时给 `--total-bytes`。只对真实二进制 URL 测速，不能拿 GitHub HTML 页速度估算附件。该工具有界读样本，不保存整包；服务器不支持 Range 时也限制读取量。已知总量时报告估算剩余时间，未知就明确未知。CDN、缓存和新连接会影响结果，实际传输期间继续更新。

示例说法：“刚测得约 300 KiB/s，文件约 200 MiB，预计约 11 分钟，实际可能波动。你希望继续等、手动下载后告诉我路径，还是使用你已有的可信下载方式？” 通用测速工具将低于约 128 KiB/s、估算超过 3 分钟、无有效数据或请求失败标为需要选择；这些是可调整的体验阈值，视频工具另按其自身阈值判断。先给出实际结果和选择再执行慢速大下载。未获选择时可继续编写方案等不依赖下载的工作，不连续重试大包。不能自行切换第三方代理、输入付费 CDK，或关闭 TLS 校验。

pip/npm 是多个文件，不能从 PyPI 首页或单个小 wheel 推算整个安装时长。列出待装包/已知大小，以依赖解析后的文件或首段实际下载测速；未知总量时只报告当前速率和已知部分。安装超过 1 分钟仍未完成时给进度，不编造确定 ETA。

## 5. MaaFGO：选择已打包环境或源码

### 已有桌面包

从 [官方 Releases](https://github.com/xlxyvergil/MaaFgo/releases) 选择 Windows `*-MXU.zip`，完整解压。普通客户端包含运行环境；**本技能仍需单独可配置的 MaaMCP**。确认当前包包含任务所需的试炼 Runtime、编队编辑器及预设。若发布包落后于开发功能，说明差异后使用源码隔离目录；不要混合不同版本的 `agent`、资源及 Python 环境。

### 需要源码策略或开发适配

无 Git 时从[开发仓库](https://github.com/dingi8528/MaaFgo_NEW)选 **Code → Download ZIP**。有 Git 时可用：

```powershell
git clone --depth 1 https://github.com/dingi8528/MaaFgo_NEW.git 'D:\Games\MaaFgo_NEW'
```

源码 ZIP 不是 Windows 桌面安装包，不保证包含客户端、内置 Python 或编辑器编译产物。安装 Python（建议 3.12；实际最低版本按项目和依赖声明），在项目目录创建独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt maa-mcp
```

为源码编辑器安装项目要求的 Node.js（参考当前构建工作流），并在项目根目录运行：

```powershell
npm --prefix web/battle-editor ci
npm --prefix web/battle-editor run build
```

Node.js 只用于构建前端，已打包编辑器不需要重建。不要让用户为原生战斗额外安装 BBC。OCR 模型、ONNX 模型及依赖是否齐全按项目 README/MaaMCP 检查，不复用作者电脑上的打包版 site-packages 路径。

## 6. 配置 MaaMCP

依据 [MaaMCP 官方文档](https://github.com/MAA-AI/MaaMCP) 在宿主新增 stdio MCP 服务。直接使用隔离环境的绝对解释器路径：

```json
{
  "mcpServers": {
    "MaaMCP": {
      "command": "D:\\Games\\MaaFgo_NEW\\.venv\\Scripts\\python.exe",
      "args": ["-m", "maa_mcp"]
    }
  }
}
```

上面是通用 `mcpServers` JSON 格式；不要直接写进不采用该格式的宿主配置。优先通过宿主 MCP 设置界面添加“命令”和“参数”，宿主需要 TOML 等格式时按它的当前说明转换。保留其他 MCP 服务及原配置备份。服务应能列出 `find_adb_device_list`、`connect_adb_device`、`screencap`、`run_pipeline`、`stop_pipeline`；以本次 tools/list schema 为准。

遇到 MuMu 发现或停止兼容问题，可使用本包主技能 `scripts/maa_mcp_bootstrap.py`（安装目录必须替换为实际路径），让 MCP 参数改为：

```text
<技能安装目录>\maafgo-hellish-trial\scripts\maa_mcp_bootstrap.py
--data-dir
D:\Games\MaaFgo_NEW\output\maa-mcp-data
--adb-path
<本机实际的MuMu adb.exe完整路径>
```

每行是一个参数；不需要 `-m maa_mcp`。`--adb-path` 可省略，按本次安装位置查找；不要照搬别人的端口。这个脚本随本技能分发，不依赖开发者本机的私有工具。某些本地工程可能另有 `tools/start_maa_mcp.ps1`，应先检查它是否公开存在及是否绑定其他机器的 venv 路径。使用 MCP 实际虚拟环境的解释器调用随包 bootstrap，不要修改全局 site-packages。

Agent 的 `child_exec` 必须指向装有项目依赖、与 MCP 使用兼容 MaaFramework 的解释器；优先在临时资源副本的 interface 中调整，不为此改写用户正式包版本字段。具体资源层、Agent、Option 解析及设备验证见配套 MCP 技能。
