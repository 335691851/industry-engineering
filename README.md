# 工程解析平台

面向机械制造工程师的智能工作台：上传装配图后，由工程 Agent 提取证据、生成并校核多级 MBOM，再按最下级零件到上层部件的顺序生成相似图纸输入、制造图和工艺流程单。每个阶段均需人工审核，审核结果才会成为下一阶段输入。

系统输出是工程草案。尺寸、公差、材料、加工余量、热处理、焊接和检验要求均区分来源与确认状态；证据不足时保留待复核项，不用固定案例参数补齐。

## 当前架构

| 层 | 实现 | 说明 |
| --- | --- | --- |
| Web 工作台 | React、TypeScript、Vite | 工程会话、MBOM、图纸、工艺、零件管理 |
| 在线 CAD | MLightCAD、ezdxf | DXF 编辑、标注、布局预览和版本保存 |
| 业务与 Agent | FastAPI、DeepAgents、LangChain、LangGraph | 专项 Agent、工具调用、阶段门禁和会话恢复 |
| 工程运行时 | PyMuPDF、RapidOCR、OpenCascade/OCP、LibreDWG | PDF/OCR、工程计算、CAD 转换和制图 |
| 云端持久化 | Supabase Postgres、Auth、Storage | 匿名工作区、租户隔离、私有文件和检查点 |
| 部署 | Vercel 三项目 | `platform`、`agent-api`、`engineering` |

Agent 架构、调用链、记忆、工具和工程边界见 [Agent说明.md](Agent说明.md)。

## 本地运行

需要 Python 3.12+、Node.js 20+。

```powershell
python -m pip install -r requirements.txt
npm ci
npm run build
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。前端开发可运行 `npm run dev`，Vite 会把 `/api` 代理到本地后端。

复制 `.env.example` 为 `.env.local` 并填写模型配置。密钥文件已被 Git 忽略，不应写入源码、文档或日志。可选 CPU OCR 与独立视觉模型配置均在 `.env.example` 中说明。

## 工程工作流

1. 上传 PDF、DXF 或 DWG 装配图，并填写装配体名称和图号。
2. 解析文字、图像和定位证据，生成可编辑的多级 MBOM。
3. 人工审核 MBOM 的制造边界、上下级、用量、材料和复用关系。
4. Agent 从最下级对象开始检索或接收相似图纸，生成当前对象制造图。
5. 工程师在参数抽屉或在线 CAD 中修改，重新生成并审核图纸。
6. 以已审核图纸和工程模型生成本件成品的核心工艺路线，修改并审核。
7. 下级成果完成后解锁上级部件；最终保存到零件管理。

明确按钮与自然语言会话使用同一组业务工具和状态门。Agent 可以查询、解释、规划和修改草案，但不能代替人工审核。

## 工程与格式边界

- PDF 原生文字与 OCR 结果保留页码、坐标、方法和置信度；引用存在不代表工程含义已确认。
- 工程中间模型保存成品尺寸、原料尺寸、加工余量、特征、来源、错误和警告，确定性规则负责数值与拓扑校核。
- 参数化出图覆盖筒体、环板和阶梯轴等已实现对象。复杂孔槽、螺纹、焊缝、基准体系和专用符号仍需专业 CAD 复核。
- PDF/DXF 是当前可靠交付格式。DWG 使用 LibreDWG 导入和受校验导出；回读不通过时不会发布 DWG。
- 在线 CAD 保存会创建新版本并使下游审核失效；CAD 图元修改不会自动反算全部参数化工程字段。
- 企业生产使用前仍需结合授权标准全文、企业工艺规范和有资质工程师审批。

## 云端部署

同一 GitHub 仓库建立三个 Vercel 项目，根目录分别为 `apps/platform`、`apps/agent-api`、`apps/engineering`。Supabase 提供匿名身份、Postgres 与私有 Storage。部署、迁移、环境变量和验收步骤见 [Hobby 三项目部署](docs/Hobby三项目部署.md)。

修改根目录源码后执行：

```powershell
npm run build
python scripts/sync_hobby_projects.py
python scripts/sync_hobby_projects.py --check
```

`apps/agent-api/server`、`apps/engineering/server` 与 `apps/platform/public` 是部署副本，根目录源码为唯一编辑源。

## 验证

```powershell
python -m pytest tests -q
node --test apps/platform/tests/*.test.mjs
npm run build
python scripts/sync_hobby_projects.py --check
python scripts/check_hobby_bundle.py
```

GitHub Actions 还会构建两个 Linux 容器，并在工程容器内执行原生依赖 smoke test。案例评分脚本位于 `scripts/evaluate_engineering.py`，测试基线位于 `tests/fixtures/`；这些数据仅用于验收，不会注入生产 Agent。

真实收卷轴案例包含两层 MBOM、辊筒关键交付尺寸和总装核心工艺的独立验收配置。若本机安装了用户提供的 `sample/示例` 文件，运行 `python -m pytest tests/test_sample_acceptance.py -q` 还会核对原始文件哈希、PDF 可读性、7 张部件图和工艺卡内容；CI 在没有业务附件时仍验证同一验收器的结构和判定逻辑。

## 目录

| 目录 | 用途 |
| --- | --- |
| `src/`、`public/` | 前端与 CAD 静态源文件 |
| `server/` | 本地及云端业务、Agent、工程计算与导出源码 |
| `apps/` | 三个 Vercel 项目的可部署目录 |
| `supabase/migrations/` | 数据库、RLS、任务和检查点迁移 |
| `scripts/` | 同步、迁移、验收和原生 smoke 脚本 |
| `tests/` | 业务、Agent、工程、云端和 CAD 回归测试 |
| `docs/` | 当前部署、CAD、质量和 DWG 说明 |

进一步阅读：[CAD Studio 集成](docs/CAD-Studio集成.md)、[工程质量与外部资源](docs/工程质量与资源.md)、[开源 DWG 替代方案](docs/开源DWG替代方案.md)。
