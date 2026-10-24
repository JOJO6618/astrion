# Astrion 内置技能库升级记录

核对日期：2026-10-24。此文件供维护者查阅来源、变更和验证情况，不是技能运行时必须读取的通用要求文件。各技能的 Astrion 适配直接写在自身原文和相关专题参考里。

## 本轮范围

源码库 `agentskills/` 从 11 个技能扩展到 31 个：更新 5 个，新增 20 个。框架专用的子智能体、命令、终端、MCP 配置与工作流等技能维持原有实现。

### 已有技能升级

| 技能 | 主要变化 |
|---|---|
| frontend-design | 更新上游设计方法；适配独立项目、聊天展示和预览交付；作品视觉语言不受 Astrion 应用源码配色限制 |
| docx | 更新 OOXML 工具与验证器；按实际路径/Python/依赖执行；批注默认署名 Astrion；修订接受脚本使用独立临时配置并拒绝未验证成功 |
| pptx | 更新原生图表、模板编辑、关系校验与视觉检查方法；移除预装依赖和固定沙箱假设 |
| skill-creator | 保留起草、用例、基线、评分、盲评与评估查看器；改为 Astrion 命名子智能体、真实计量和归档流程；原 CLI 触发优化保留为显式选择的外部集成 |
| ui-aesthetic-design | 保留工具界面专业内容；明确适用边界，补键盘、焦点、触屏、减少动画与真实主题检查 |

### 新增技能

| 领域 | 技能 | 具体用途 |
|---|---|---|
| Office | xlsx、pdf | 工作簿、公式重算；PDF 提取、表单和页面处理；两者完整保留固定上游原样 |
| 动画与媒体 | remotion-best-practices | React 程序化视频、地图、字幕、交互预览、导出与渲染 |
| 动画与媒体 | manimce-best-practices | 数学、科学、三维教学动画；仅 Community Edition，不混入 ManimGL |
| 动画与媒体 | media-toolkit | Astrion 自写的 FFmpeg/ffprobe/Pillow 工作流：剪辑、转码、字幕、音轨、封面、拼图与批量处理 |
| 视觉创作 | canvas-design、algorithmic-art | 海报和静态作品；p5 种子生成艺术与交互探索 |
| UI 设计与评审 | impeccable、web-design-guidelines | 多类型界面设计、细化、审计、动效与可访问性；完整方法和配套资源保留 |
| 前端工程 | react-best-practices、composition-patterns | React/Next 性能与组件组合；按实际框架版本应用 |
| 研究 | scientific-visualization、literature-review | 可复现科研图表、导出检查；文献检索、筛选、证据综合与引用核对 |
| 写作 | doc-coauthoring、copywriting、copy-editing | 文档协作与读者测试；营销文案起草和精修 |
| 营销 | emails、product-marketing | 邮件序列设计；产品、受众、定位与已验证经验的项目上下文 |
| 开发 | mcp-builder、webapp-testing | MCP 服务开发与验证；Playwright 功能测试与浏览器取证 |

## 来源与固定版本

完整 commit、原始文件 SHA256、直接适配的改动/新增/删除文件及其当前 SHA256 见 `agentskills/UPSTREAM_LOCK.json`。每次刷新应选择明确版本后重新核验，而非仅标成“最新”。

| 来源仓库 | 固定 commit 前缀 | 引入内容 |
|---|---|---|
| anthropics/skills | dbd4588f9e10 | frontend-design、skill-creator、Office、生成艺术、平面设计、协作文档、MCP、Web 测试 |
| remotion-dev/skills | 6c67adcd9cdb | Remotion 完整聚合技能，参考标记版本 4.0.538 |
| adithya-s-k/manim_skill | cef045011722 | Manim Community Edition 技能与示例 |
| K-Dense-AI/scientific-agent-skills | 593377bb0487 | 科研绘图和文献综述 |
| vercel-labs/agent-skills | 063bee94c3f4 | React 性能和组件组合 |
| vercel-labs/web-interface-guidelines | 434b7f913646 | 本地固定的完整网页审查规则 |
| pbakaus/impeccable | fdb532263578 | 编译后的完整 Impeccable 技能；包含原 CLI、参考和资源 |
| coreyhaines31/marketingskills | e85f3cbc2e4a | 文案起草、精修、邮件与产品营销上下文 |

技能目录原有许可证保留；必要的仓库许可证与 Impeccable NOTICE 一并引入。生成艺术查看器附带 p5 1.7.0 与许可证，以避免强依赖 CDN；另从字体项目来源补齐 IBM Plex Serif 和 Instrument Serif 的 OFL 文本。补充资源地址记录在 lock 文件中。

完整下载快照与原技能备份保存在未追踪目录 `_experiments/skills-refresh-2026-10-24/`，不作为正式内置技能分发。当前正式库约 17 MB，暂存快照约 673 MB；本轮未清除备份。

## 直接适配的主要边界

- 不再存在 `UPSTREAM_SKILL.md` 或统一 `astrion-runtime.md`。入口保留各自完整的专业论述，修改不适用的原段落。
- 脚本和字体路径相对实际加载的 SKILL.md；用户输出保存在工作区，不写入安装目录。
- Python、Node 包、浏览器、Office、TeX 等先探测再使用；不假设任何云端工作机的预装环境。
- 子智能体使用 Astrion 的命名与续接接口，尊重实例容量、上下文隔离限制及用户关于主要实现的要求，完成后保留 idle。
- 原文件、用户作品、应用源码分别按其实际用途处理。聊天图解和固定构图采用实际支持的 show_ui/show_html 协议；独立作品保留合理文件结构。
- 持久服务器提供真实完整 localhost 地址供右侧预览；实际文件通过 show_file、show_image 或下载链接交付。
- Impeccable 保留其 CLI、detector、live/generate 和可选外部编辑器 hooks；Astrion 不读取其他编辑器的 hooks。首次引擎下载及写入范围应说明，未运行的检测不能声称通过。
- 原 skill-creator 的 `run_eval.py` / `run_loop.py` / `improve_description.py` 使用外部 Claude CLI，不能冒充 Astrion 通用模型评估后端；默认在实际目标模型/工具上下文中评估触发。
- React 和 Manim 的多行 description 已改为单行，确保当前 Astrion 目录解析器可以显示真实描述。
- 本轮仅维护源码 `agentskills/`；`.astrion/skills/` 由既有同步机制维护，未手工覆盖 `.agents/skills/`。静态目录检查不等于当前已注入对话清单即时热更新。

## 验证结果

可复现入口在 `test/2026-10-24_内置技能升级/`。

| 验证 | 结果与范围 |
|---|---|
| 技能元数据与真实目录解析器 | 31 个全部通过；测试只提取实际解析函数，不加载真实配置/.env、不启动服务 |
| Python 语法 | 94 个技能脚本用 Python 3.11 AST 解析通过；不等同所有依赖及功能均可运行 |
| Markdown 资源链接 | 全库 312 个 Markdown，325 处相对链接目标核查，剩余缺失 0；未全面验证页内锚点、外链或每个引用的语义 |
| xlsx 上游原样 | 53 文件：无新增、无删除、无 SHA256 差异 |
| pdf 上游原样 | 12 文件：无新增、无删除、无 SHA256 差异 |
| p5 查看器 | HTML 本地资源均存在，JS 经 node --check；未启动浏览器或做视觉验收 |
| 修订接受脚本 | 4 项回归通过：配置隔离与清理、超时不伪报成功、修订残留不发布、保留输入 |
| Office 代表性 CLI | comment、merge_runs、validate、accept_changes、add_slide、thumbnail 的 --help 通过；不等同真实 Office 渲染通过 |
| FFmpeg 真实小样例 | 合成 160×120 H.264 视频，ffprobe 元数据核对和全片解码通过 |
| 科研绘图辅助脚本 | 调色板检查、合成 PNG 元数据检查真实运行通过 |
| skill-creator 查看器 | 用明确标注的合成用例生成静态 HTML，确认结果嵌入；没有运行真实模型基准 |
| Word 真实修订接受 | 未通过实机冒烟：LibreOffice 初始化独立配置时 Abort trap；版本查询正常，原因未确定。未将该项标成成功 |

上述升级验证阶段没有安装重依赖、启动/重启 Astrion 后端或运行截图替代用户验收。后续对照实验与 Git 提交按用户另行授权进行；提交前再次通过目录校验与 4 项修订脚本回归。暂存检查发现上游原文和冻结实验产物存在空白格式提示，为保留原样未批量格式化。

提交资源时需核对完整技能目录：仓库的 `data/` 忽略规则会匹配 Impeccable 的 `scripts/data/`，本轮已显式纳入两份固定上游字体索引；运行缓存不纳入提交。

运行命令（以本机已验证解释器为例）：

```bash
/opt/homebrew/bin/python3.11 -B test/2026-10-24_内置技能升级/audit_library.py
/opt/homebrew/bin/python3.11 -B -m unittest discover -s test/2026-10-24_内置技能升级 -p 'test_*.py'
node --check agentskills/algorithmic-art/templates/js/app.js
/opt/homebrew/bin/python3.11 -B test/2026-10-24_内置技能升级/smoke_tools.py
```

最后一项会执行真实 FFmpeg 与 LibreOffice；目前 LibreOffice 部分仍失败，应保留失败信息继续诊断，不把版本查询或 mock 回归当成真实修订处理成功。

## 依赖与已知限制

本机探测到 FFmpeg、ffprobe、LibreOffice、Pandoc，以及所选 Python 3.11 的 Pillow、pypdf、openpyxl、defusedxml、lxml、requests、PyYAML。系统默认 python3 实际为 3.9.6，不能运行部分新版脚本；技能要求先选兼容解释器。

本轮未探测到项目可直接 resolve 的 `docx`、`pptxgenjs`，也未探测到 Manim、uv、所选 Python 的 Matplotlib/Playwright 或 `pdftoppm`。这些按具体任务准备；没有为技能维护自动安装全部运行环境。Remotion 成片、Manim 动画、Office 完整生成/渲染、Impeccable 引擎与真实模型触发/效果测试均未完整实跑。

按用户要求，pdf 保留上游原样，包括其纯文本 `REFERENCE.md` / `FORMS.md` 提及与实际小写文件名之间的差异；没有加链接或改文档掩盖它。xlsx、pdf 的逐字节原样保证不等于上游没有文档或环境问题。
