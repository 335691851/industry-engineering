# 开源 DWG 转换替代 ODA

## 选型（2026-09-28）

| 项目 | 适配情况 | 本系统选择 |
| --- | --- | --- |
| [LibreDWG](https://github.com/LibreDWG/libredwg) | C 库及 dwg2dxf/dxf2dwg CLI，GPL-3.0-or-later；读取范围较广，写出存在兼容性限制 | 服务端采用 0.14 固定版本，替换 ODA 调用 |
| [libdxfrw](https://github.com/LibreCAD/libdxfrw) | C++ 库；当前 README 将 DWG 读写标为 experimental，需要独立语义样例验收 | 保留为备选，暂不同时引入另一套原生库 |
| [libredwg-web](https://github.com/mlightcad/libredwg-web) | LibreDWG 的浏览器/Node WASM 封装 | 可做浏览器读取，但不能解决同一内核的写出兼容性问题 |

## 已实现

- server/dwg_converter.py 统一执行开源 CLI，不再调用或检测 ODA。
- 装配 DWG 导入、CAD Studio 打开和上传均转到 LibreDWG → DXF → ezdxf。
- DWG 写出限定 R2000；不声称支持可靠写出 R2018。写出后再次读取，对图元属性、文字、尺寸块、布局与单位作保守比较；失败不发布 DWG，保留 PDF/DXF。
- 转换失败原因保存至 geometry.dwg_warning，在导出区和生成摘要中展示。
- 原图不覆盖；转换在独立临时目录使用固定文件名，90 秒超时，原生子进程输出写入临时日志。
- Dockerfile.cloud 已增加 LibreDWG 源码构建阶段；Vercel 打包器会自动继承。源码归档固定 SHA256，镜像带 COPYING 和对应源码。

## 实测结果与限制

本机使用官方 0.14 Windows 预编译工具，现有 output/cad-review/tube-layout.dwg 导入成功：Model 中 6 个实体，Model、Layout1、A3-1 三个布局。该文件为现有 CAD 验收样例，不等同于所有真实生产 DWG 已验收。

该样例的再导出，以及简单圆形/文字 DXF 的写出测试，发现结构错误、无效句柄或图元丢失。系统已阻止将这些结果当作成功 DWG 交付。记录在 output/libredwg-validation.json。

**因此当前完成的是 ODA 依赖替换和受校验的开源转换链路，不是完整 DWG 兼容性替代。现阶段可靠交付格式仍为 PDF/DXF；DWG 导出只在回读门禁通过时提供，并仍需外部 CAD 软件验收。** 自回读不构成独立的 AutoCAD/其他 CAD 兼容性证明。

导入亦可能遗漏不支持的高级对象，界面提示复核复杂标注、填充和布局。需要更多原始业务 DWG 样例建立外部基准。Linux 镜像尚未构建，需在 Vercel 构建环境验收原生工具。

## 安装

云端：运行现有 scripts/package_vercel_platform.py 后部署输出目录，Dockerfile 自动下载固定版本源码并编译，无需 ODA 包或 ODA 订阅。

Windows：从官方 [0.14 Release](https://github.com/LibreDWG/libredwg/releases/tag/0.14) 下载 libredwg-0.14-win64.zip，解压后将 LIBREDWG_BIN 指向含 dwg2dxf.exe、dxf2dwg.exe 及 DLL 的目录。本机已放置于 .tools/libredwg/win64，适配器可自动发现。

Windows 包 SHA256：1ad7e15344d20b3426c3435b078d82fb84b35062815946b2cca9c5fc9810fea8。

Linux 源码 SHA256：62ebb73b984f865960f20ed26619ea5f8789d5e3fd088fa40a2598384da81275。

## 许可

LibreDWG 不是免义务的软件：遵循 GPL-3.0-or-later。镜像保留版权许可与本次构建源码；分发镜像或软件时需履行适用的源码提供义务。独立进程调用是工程边界，不应据此断言所有集成和分发场景自动豁免 GPL 要求。项目未将 GPL 库直接链接进 Python。
