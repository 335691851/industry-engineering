# Vercel Hobby + Supabase：三个独立项目部署

当前主仓库可以直接连接 GitHub。三个目录分别导入为三个 Vercel 项目；不使用 Services 绑定，也不需要 Cloudflare，也无需 ZIP。

### 直接使用现有仓库（推荐）

已将 server 源码、依赖和前端构建同步到三个 apps 目录，可以直接提交。后续修改根目录 `server/` 或 `src/` 后，提交前执行：

```powershell
npm run build
python scripts/sync_hobby_projects.py
python scripts/sync_hobby_projects.py --check
```

将 `apps/`（含同步文件）、根目录源码、scripts、依赖锁文件、supabase、docs 和 `.github/workflows/verify.yml` 一并提交。Vercel 只需要读取对应 apps 项目目录。生成副本以根目录源码为准，不要直接编辑 apps 下的 server 副本。GitHub Actions 会检查副本是否同步，并构建 Linux 容器。

以下部署顺序、环境变量和验收步骤适用于直接连接仓库；涉及部署包、frontend-source 和 manifest 的说明仅适用于此前生成的 ZIP，不是直接部署的前提。

## 目录与顺序

| 项目 | Vercel Root Directory | Framework | 职责 |
|---|---|---|---|
| engineering | `apps/engineering` | 显式 Services 容器配置 | 逐页 OCR、CAD/PDF/DXF、LibreDWG |
| agent-api | `apps/agent-api` | 显式 Services 容器配置 | 业务 API、DeepAgents、任务记录 |
| platform | `apps/platform` | Next.js | 已构建前端、同源 API 代理、Workflow |

建议先创建三个项目并记录各自的稳定 Production 域名，再配置变量并重新部署。可以连接同一个 GitHub 仓库，根目录不同。不要把本仓库根目录直接作为一个 Vercel 项目部署。

容器项目不填写 npm 构建命令，也不要在 `functions` 中填写 `Dockerfile.vercel`：该字段用于匹配函数源码，不是 Docker 构建入口。两个后端的 vercel.json 通过 services.backend 显式声明 root="."、runtime="container"、entrypoint="Dockerfile.vercel"，并以 /(.*) rewrite 暴露服务。Services 模式下不在顶层设置 framework/functions。启用 Fluid Compute，并在项目控制台核对函数默认时限为 300 秒、内存为 2 GB。Vercel 应识别根目录的 `Dockerfile.vercel`。平台使用 `npm ci` 与 `npm run build`。三个项目都启用 Fluid Compute，区域尽可能靠近 Supabase。平台 public 已包含网页和 CAD 编辑器，首次部署无需重新构建 Vite。

## 1. Supabase 初始化

1. 创建 Supabase 项目。在 SQL Editor 按顺序执行：
   - `supabase/migrations/20260928005941_engineering_cloud_business.sql`
   - `supabase/migrations/20260928020000_vercel_task_dispatch.sql`
   - `supabase/migrations/20260928040000_hobby_activities.sql`
2. 上述第一份迁移会创建业务表、`engineering_app` 角色、租户 RLS 和 `engineering-private` 私有 bucket。初次安装执行一次，不要重复运行第一份迁移。
3. 获取 Session pooler 的连接串（5432，携带 `sslmode=require`）。不要使用 6543 Transaction pooler：当前项目锁和 LangGraph 检查点依赖连接上下文。
4. 初始化 Agent 检查点：新建库可在 SQL Editor 执行 `supabase/migrations/20260928050000_agent_checkpoints.sql`（与当前安装的 PostgresSaver 迁移版本 0–9 对齐）。已经初始化的库不要重复执行此 SQL。也可在自己的电脑终端执行下述脚本：

   ```powershell
   python -m pip install "psycopg[binary]>=3.2,<4" "langgraph-checkpoint-postgres>=3,<4"
   $env:SUPABASE_DB_URL = "你的 Session pooler 连接串"
   python scripts/setup_cloud_checkpoints.py
   ```

   连接串仅放环境变量，禁止写入仓库。密码含特殊字符时使用 URL 编码。脚本创建检查点表并加租户策略，不会删除业务数据。
5. 在 Authentication 中创建测试用户（邮箱和密码）。当前前端提供登录，不提供自助注册；首次用户从 Dashboard 创建。配置 Site URL 为平台域名。

已有云端业务库只需执行尚未应用的迁移；部署默认不上传本地业务数据、不导入历史样例，也不清空云端库。

## 2. 环境变量分配

每个项目都有 `.env.example`。将值填写到 Vercel 项目 Settings → Environment Variables。全部是服务端变量，不要改成 `NEXT_PUBLIC_*` / `VITE_*`。

### 三个项目共用

- `ENGINEERING_SERVICE_TOKEN`：相同的随机密钥，至少 32 字符。可在终端执行 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成。

### platform

- `ENGINEERING_BACKEND_URL`：Agent 项目 Production 地址，如 `https://my-agent.vercel.app`。
- `CRON_SECRET`：另一个随机密钥，至少 32 字符。
- 可选 `ENGINEERING_BACKEND_BYPASS`：Agent 项目开启 Deployment Protection 时的自动化绕过密钥。

### agent-api

- `ENGINEERING_APP_ORIGIN`：平台 Production 地址，协议与域名必须完全一致。
- `ENGINEERING_ORCHESTRATOR_URL`：同一个平台地址。
- `ENGINEERING_NATIVE_URL`：engineering 项目地址。
- `SUPABASE_URL`、`SUPABASE_PUBLISHABLE_KEY`、`SUPABASE_SECRET_KEY`、`SUPABASE_DB_URL`。
- `SUPABASE_STORAGE_BUCKET=engineering-private`。
- `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_TEXT_MODEL`、`DEEPSEEK_VISION_MODEL`。填写账号实际可调用的模型标识；部署不会自动验证模型的图像能力。
- `PORT=80`；容器中已设置 cloud 模式与 vercel 调度方式。
- 可选 `ENGINEERING_ORCHESTRATOR_BYPASS`、`ENGINEERING_NATIVE_BYPASS`：分别对应平台和工程项目的 Deployment Protection。

如使用其他视觉 API，再设置 `ENGINEERING_VISION_BASE_URL`、`ENGINEERING_VISION_API_KEY`、`ENGINEERING_VISION_MODEL`，三个必须同时配置。

### engineering

- `SUPABASE_URL`、`SUPABASE_SECRET_KEY`、`SUPABASE_STORAGE_BUCKET=engineering-private`。
- `PORT=80`，`ENGINEERING_OCR=auto`。
- 不需要数据库连接串、DeepSeek Key 或用户登录配置。

更改环境变量后重新部署。最初只验证 Production 域名，不要将 Preview 与 Production 的三个地址混用。若跨项目请求收到 Vercel 登录页/401，检查 Deployment Protection；服务自己的鉴权仍然必须保留。

## 3. 执行与恢复

浏览器 → platform 同源代理 → agent-api。生成请求写入 Supabase 后返回 202；平台 Workflow 以任务 ID 和租户 ID 调用 Agent。Agent 将 OCR/CAD 输入放私有 Storage，再调用工程服务；文件结果也通过 Storage 返回。

- 平台函数配置上限 300 秒、2048 MB；容器使用项目的 Hobby / Fluid 默认资源设置，请在控制台核对。Agent 子进程最多 240 秒。
- 工程原生子进程最多 100 秒，超时终止子进程；远程调用最多 115 秒。
- 模型调用 95 秒，无 SDK 内部重试叠加。已完成模型响应、逐页 OCR、原生输出和成功提交结果写入 `cloud_activities`。
- 在下一个耗时步骤开始前检查预算；预算不足将任务置回 queued，Workflow 再执行一个分段，复用完成的活动。每个任务最多 40 个分段。
- 工程会话使用任务独立的 LangGraph 检查点，保留业务历史；同一任务恢复时继续其挂起图执行。
- 每个项目用 PostgreSQL advisory lock 串行执行。输出保存保留原有版本检查与人工审核门。
- 模型异常、无法处理的图纸、硬超时、进程崩溃会明确失败，不盲目重放未知完成状态的写入；用户检查已有成果后重新提交。
- 每天 03:00 UTC 扫描遗漏调度；正常恢复依赖 Workflow 与页面轮询，不依赖五分钟 Cron。

这是活动结果级恢复，不是任意机器指令位置的断点恢复。单个 OCR 页面、模型响应或 CAD 操作本身必须能在预算内完成。更大的文件需拆分；多个项目不会延长单次运行限制，也不会增加账号总额度。

## 4. 首次验收

1. engineering `/health` 应返回 `service=engineering`；无服务密钥 POST `/execute` 必须返回 401。
2. platform `/index.html` 可打开，`/api/deployment` 返回 cloud；未登录访问业务数据返回 401。
3. 使用 Supabase 中创建的用户登录。刷新页面仍能读取项目，退出后不可读取私有数据。
4. 上传一个小 PDF，确认上传成功并出现预览；生成 MBOM，刷新页面观察任务是否继续。
5. 审核 MBOM → 确认相似图输入 → 生成单个零件图 → 人工审核 → 生成工艺 → 保存。未审核不得越过阶段。
6. 测试 CAD DXF 导入和编辑保存，下载 PDF/DXF，核对几何与标注。DWG 不通过回读校核时必须保留错误提示。
7. 换第二个账号，确认不能读取第一个账号的项目、任务和 Storage 文件。
8. 查看三项目日志，确认没有 504、OOM 或数据库连接耗尽。真实模型耗时与原生内存占用需要在实际环境验证。

## 5. GitHub 自动检查

部署包包含 `.github/workflows/verify.yml`：检查三项目配置、构建 Next.js、构建两个 Linux 镜像并运行原生组件 smoke test。应先查看 GitHub Actions 结果，再将 Vercel 部署提升为可用版本。Actions 消耗 GitHub 自身的构建额度。

本地尚未执行 Docker/Linux 镜像构建及真实云端端到端验收，不能把静态检查与 Windows 测试当成这些验收已完成。

本次本地验证：65 项 Python 回归、3 项平台 Node 测试、Postgres 迁移与 RLS 测试通过；Vite 与 Next.js 构建通过；构建后 Next.js 的 Workflow 实际调度、鉴权与分段恢复联调通过（后端桩，不消耗真实模型）。真实云端模型质量、容器内存占用、Linux 原生组件兼容性仍待部署后验收。

## 6. 限制与更新

- Hobby 官方只允许个人非商业用途。多个项目不改变此范围。
- Container Images 是 Beta，需要账号可使用该能力；本方案依赖它运行 Linux 原生组件。若控制台未开放，不能退化成 Edge Function 运行这些库。
- DWG 导入按实际文件验证。LibreDWG 写出兼容性有限，当前可靠交付以 PDF/DXF 为主，详见 `DWG说明.md`。
- 容器包含 OCR/OpenCascade，不等于所有复杂文件都能在 2 GB 内处理；不要并发提交大量原生任务。
- `manifest.json` 是本次打包文件的校验清单。仓库不包含 `.env.local`、本地数据库、用户案例或模型密钥。
- `frontend-source` 包含可编辑前端源码，部署网页来自 `apps/platform/public`。通常在主工程修改后重新运行 `npm run build`、`python scripts/package_hobby.py` 更新部署包。如果只修改解压后的仓库，在 frontend-source 执行 `npm ci`、`npm run build`，把生成的 dist 内容同步到 apps/platform/public 再提交。单独修改 frontend-source 不会自动更新已经构建的网页。

官方依据：https://vercel.com/docs/functions/container-images 、https://vercel.com/docs/functions/limitations 、https://vercel.com/docs/plans/hobby 、https://vercel.com/docs/cron-jobs/usage-and-pricing 。

### 空部署排查

构建只有几十毫秒且没有安装依赖或镜像构建记录时，Ready 不代表容器已运行。使用最新显式 services 配置重新部署，检查 Agent `/api/deployment` 返回 cloud/authentication JSON，engineering `/health` 返回 engineering/ok。云端实际构建和运行仍需验证。
