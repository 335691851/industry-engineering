# 工程解析平台

> DWG 转换已改为 LibreDWG，详见 [开源替代与兼容性边界](docs/开源DWG替代方案.md)。下方历史 ODA 安装说明不再适用于当前后端。

> **当前云端方案：Vercel Hobby 三个独立项目 + Supabase。** 资源申请、架构、打包和部署步骤见 [部署说明](docs/Hobby三项目部署.md)。直接连接现有 GitHub 仓库，分别选择 `apps/platform`、`apps/agent-api`、`apps/engineering` 作为三个项目的根目录；提交前执行 `npm run build` 和 `python scripts/sync_hobby_projects.py`，无需打包。下方三平台内容为历史方案；新部署无需 Cloudflare。真实云端与 Linux 原生运行时尚待资源开通后验收。

面向机械制造工程师的本地 Web 工作台：上传装配图，识别和编辑多级 MBOM，由专业 Agent 生成当前部件的制造图、放量方案及工艺卡，逐阶段人工审核后进入上层并归档。首次启动为空工作区；如需载入示例包中的装配图、部件图和工艺卡，可在环境变量中设置 `ENGINEERING_LOAD_SAMPLE_DATA=1` 后重启。

## 启动

### 云端部署进度

以下为历史三平台适配记录；当前请使用上方 Hobby 部署说明。历史适配说明见 [三平台部署适配](docs/三平台部署适配.md)。
已实现完整业务 API 的云端适配：Supabase 数据库、Agent checkpoint、私有文件存储、登录及 Cloudflare 持久任务。前端通过同域代理访问云端 API。
提供 Cloudflare Containers 原生运行时和 Vercel Python 受限运行时两种部署配置。代码与本地回归已验证，尚未部署到真实云账号；Linux 原生依赖及 DWG 转换仍需云端验收。详见部署说明中的验证边界。

### 本地运行

需要 Python 3.12+、Node.js 20+。

```powershell
python -m pip install -r requirements.txt
npm install
npm run build
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。开发时可运行 `npm run dev`，访问 <http://127.0.0.1:5173>，Vite 会代理 `/api` 到后端。

在项目根目录的 `.env.local` 配置 `DEEPSEEK_API_KEY`。此文件已被 Git 忽略。可复制 `.env.example` 并填写。默认使用支持图像输入的 `deepseek-flash`，并在结构化输出时关闭思考模式，避免长推理挤占 JSON 输出。模型名称可通过环境变量调整。

## 工作流程

1. 在会话窗口上传 PDF、DXF 或 DWG 装配图。系统解析图纸并创建可编辑的多级 MBOM，包括上下级关系和每上级用量。DXF 在本地渲染为 PDF；DWG 需 ODA File Converter。
2. 在 MBOM 页面审核零部件名称、上级、用量和材料，确认后点“一键生成全部”。编排 Agent 按依赖顺序推进，每阶段生成后等待人工审核，审核后继续；页面显示进度和失败项。
3. 选择对象预览图纸、工艺卡和原图。编辑尺寸、公差、工序及检验要求，或在会话窗口直接描述修改。会话自动区分图纸、工艺和问答意图。
4. 修改完成后保存单件，或点“全部保存到零件管理”。图纸可导出 PDF、DXF、DWG；工艺流程单同步提供 PDF 和可编辑 Excel。

工程记忆保存在 SQLite 的 `engineering_memory` 表，默认不自动载入样例。“零件管理 → 工程记忆”可加入企业历史零件图 PDF 和工艺 Excel（序号、工序、说明三列）。启用样例数据后，系统还会载入附件中的历史图纸、工艺流程，以及 GB/T 1804-2000 和 GB/T 1184-1996 的标准入口。标准数值须以已授权标准全文及图纸标注核对；记忆不包含自编的标准公差表。缺失成品尺寸保持未知，仅对已定位的单个缺失段做差值求解；制造放量由 Agent 提出带依据的建议，由确定性计算器得到毛坯尺寸。

## 工程边界

- 装配图常不能唯一确定单件所有尺寸。系统保留证据不足项，禁止用固定样例数值补齐；成品图、放量和工艺须按阶段审核。
- 当前二维生成器覆盖回转体外轮廓和简化圆板/环板。键槽、复杂孔系、焊缝、完整形位公差标注等仍需在专业 CAD 中完善。工程师必须审核生产图。
- DWG 是专有格式；DWG 输入与导出依赖 ODA File Converter。系统会自动发现本机 `C:\Program Files\ODA\ODAFileConverter*` 下的安装版本，也可通过 `ODA_FILE_CONVERTER` 指向可执行文件。本机已验证生成 DWG。
- 此版本是本地单用户工作台；尚未实现企业身份认证、权限、版本审批和 PLM/ERP 对接。投入企业生产前需要补齐这些能力。

会话窗口采用 LangChain DeepAgents，以 DeepSeek 的工具调用自主选择项目查询、工程记忆检索、零件图、零件工艺卡或总装工艺卡。工具仅能访问当前项目，工程数据仍由服务端验证并导出。Agent 的多轮状态保存在 SQLite 检查点，工程标准与历史样例保存在独立的 SQLite 工程记忆表。MBOM 确认、批量生成和人工审核仍是工作流的一部分。

装配图解析先由视觉模型提取图纸候选，再由 DeepAgents 审核装配层级并提交经过拓扑校验的 MBOM。`parts` 存放零件定义，`mbom_links` 存放每上级用量和装配关系；同一个零件可在多个部件下复用。与用户提供的收卷轴样例图号匹配时，系统采用“辊筒、轴头1、轴头2”一级及两个轴头下的轴和闷板作为候选结构；闷板复用位置标记为待复核，工程师可在 MBOM 页面调整后确认。

验证：`python -m pip install pytest` 后运行 `python -m pytest -q tests/test_workflow.py`，覆盖上传、MBOM 层级与循环校验、工程记忆导入、批量生成、PDF/DXF/DWG/Excel 资源和最终归档。

参考：[DeepSeek 视觉输入](https://api-docs.deepseek.com/guides/vision/)、[DeepSeek JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)、[ezdxf 的 ODA 转换支持](https://ezdxf.readthedocs.io/en/stable/addons/odafc.html)。

最新需求与实现说明：[需求与实现核查](docs/需求与实现核查.md)。图纸 SVG、PDF 与 DXF 布局共用毫米绘图场景；筒体、环板与阶梯轴以外的复杂特征仍需专业 CAD 支持。
