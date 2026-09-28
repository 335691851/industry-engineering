# 在线 CAD Studio 集成

## 选型与版本

本次采用 [MLightCAD cad-viewer](https://github.com/mlightcad/cad-viewer) 1.7.1，数据模型为 `@mlightcad/data-model` 1.14.13。核心组件 MIT 许可，中文 Vue 界面，包含命令行、实体属性、图层、绘图和修改工具、标注及模型/布局视图。它是浏览器 CAD 编辑组件，并非完整 AutoCAD 替代品。

对比过 [cadjs](https://github.com/dxfjs/cadjs)（较轻量的二维编辑）、[OpenCADStudio](https://github.com/HakanSeven12/OpenCADStudio)（Rust/桌面与 Web，GPL-3.0）。本平台已有 React、Python 和 ODA 转换链路，MLightCAD 的可嵌入界面更适合当前集成。

## 运行链路

`生成图纸 → 在线 CAD 编辑 → 同源 cad-studio.html → MLightCAD → 本地保存接口 → 原图差异合并 → DXF/PDF/可选 DWG → 图纸待审核`

- React `src/CadEditor.tsx` 只负责全屏 iframe 和保存/关闭事件；严格校验消息来源、窗口和零件 ID。
- `src/cad-studio.ts` 挂载 Vue 编辑器，独立隔离 CAD 样式和全局管理器。
- `server/cad_studio.py` 提供加载、上传、源文件、基线及保存接口。
- `server/cad_editor.py` 继续提供本地 ODA 转换和统一版本保存。
- 打开图纸不再服务器渲染 SVG，也不再生成 5000 个透明 SVG 选择框。编辑在浏览器 CAD 场景内完成，只有打开与保存访问后端。
- 文本渲染 worker 本地部署，构建前由 `scripts/prepare-cad-studio.mjs` 从锁定版本依赖复制。

## 文件及审核

DWG 经本机 ODA File Converter 转成 DXF，原始上传文件保留；没有 ODA 时 DWG 导入会给出明确错误。ODA 是独立的非开源工具，本次没有把它描述为 MIT 组件，也没有安装 GPL 的 LibreDWG 浏览器插件。

打开后记录原始 DXF 与浏览器首次序列化基线。保存时比较“首次序列化 → 编辑后序列化”，将新增、修改、删除的实体合并回原 DXF。这样浏览器未表达的原始实体和布局不会被直接清空。新增/修改实体通过 ezdxf Importer 导入资源；保存采用零件 `updated_at` 乐观锁。

CAD 新版本保存后，图纸回到待审核；旧工艺导出和上层依赖按平台原有流程失效，必须重新审核后进入下一阶段。CAD 编辑不会自动重算 Agent 的参数化几何模型。

## 当前边界

- 本次验证了实际 MLightCAD 数据模型导入/导出、直线端点修改、原生尺寸实体合并、原始布局保留、版本冲突及审核状态；17 项后端测试通过。浏览器控制工具连接失败，尚未完成鼠标交互和大型 DWG 的可视验收，不能据此宣称所有 CAD 实体完美兼容。
- 不支持保存增删布局、直接改写既有块定义和既有字体/标注/线型样式定义；接口阻止覆盖并给出提示。可修改普通实体、应用新样式、调整图层颜色/可见性/线宽等。
- 保存工具栏的布局选择用于指定 PDF/SVG 预览布局；CAD 底部布局栏用于切换编辑视图。
- 特殊代理实体、外部参照、约束和专用符号仍需专项验证。字体替代可能改变字宽，不能视为完全一致的 AutoCAD 输出。
- 编辑器内置额外 AI 面板被禁用，继续使用本系统工程 Agent。浏览器内置直接导出被禁用，使用平台保存按钮确保版本与审核一致。

## 字体和许可

MLightCAD 官方 cad-data 仓库明确要求使用者自行取得字体许可，所以没有打包其商业字体。采用 [Noto CJK](https://github.com/notofonts/noto-cjk) 的 NotoSansCJKsc-Regular.otf，本地 `public/cad-assets/fonts/OFL.txt` 保存 SIL Open Font License。字体目录中的常见字体名被映射到 Noto；专业 SHX 字形不保证等价，需要企业提供有授权的字体后扩充。

依赖在 package-lock.json 锁定。安装时 npm audit 报告 lodash-es 与 undici 及其传递依赖告警，自动修复未消除全部问题；本次集成按本机用途交付，公开部署前应处理这些依赖告警并补充鉴权、上传隔离和 CAD 文件安全测试。

## 启动

`npm install` → `npm run build` → `python launch_platform.py`。在 http://127.0.0.1:8000 的“生成图纸”模块打开“在线 CAD 编辑”，顶部加载 DWG/DXF，编辑后点击“保存为图纸新版本”。
