# macOS 154 上游文件树构建

本次使用用户指定的 [ungoogled-chromium-macos run 37654894671](https://github.com/ungoogled-software/ungoogled-chromium-macos/actions/runs/37654894671)。这是源码与编译输出缓存，不是将上游 DMG 改名为 Chromix。

## 固定来源

| 项目 | 值 |
|---|---|
| Chromium | `154.0.8037.97` |
| macOS overlay | `f7ba75f94442abda7ac3ea81790c217f8636d3ba` |
| ungoogled core 子模块 | `3e46b13825f808f0886e484d44532372655e5fe4` |
| 分支 / 工作流 | `154.0.8037.97` / `.github/workflows/build.yml` |
| 上游运行 | `37654894671`，attempt 1，completed/success |
| 内层归档与源码根 | `build_src.tar.zst` / `src` |

macOS 的 core 与 Linux/Windows 使用的 SHA 不同，必须按真实 gitlink 验证；两份 core 的差异目前仅在 CI 文件，不能据此省略身份绑定。

| 目标 | 文件树 artifact | 外层 ZIP 字节数 | SHA-256 |
|---|---:|---:|---|
| x64 | `11527088964` (`github_build_artifact_x86_64`) | 12505793478 | `f79f7e5769b25d588ab68aa8c2963caeb40e04504b375a97e7288ee77467f186` |
| arm64 | `11520487436` (`github_build_artifact_arm64`) | 10872477177 | `4fc2c995fc01213d3e819ff05db169fb14f0f93487b387f23cbf9a7b2e493e6a` |

## 恢复与适配要求

- 版本和缓存身份集中在 `CHROMIUM_MACOS_VERSION`、`build/ungoogled-revisions.psd1`、`build/upstream-cache.json`。
- 224 个 Chromix 补丁使用 macOS154 的严格身份选择，复用经过摘要认证的 Chromium154 覆盖补丁。历史 macOS152 选择仍保留给旧证据和迁移验证。
- 新上游 mac 层采用系统 esbuild/TypeScript，并产生指向源码树外的 npm 模块链接。仅接受精确文件树身份与允许的链条；不能忽略所有外链。
- 生成器恢复须验证工具来源、内容、宿主架构和源码输入。修改工具或生成器输入后应使相关 Ninja 生成结果重新计算，不能声称全部旧对象均可复用。
- bindgen loader 路径修复必须匹配对应版本的原始/修复源码摘要；不得将未知脚本当作已验证输入。
- x64 donor 构建使用了 ARM64 宿主工具。目标架构与编译宿主不同，恢复时需要校验本机工具格式，并根据已固定平台资源获取兼容宿主工具。

## 调度

在配置和相关适配测试通过后分别调度：

```bash
gh workflow run build-macos-x64.yml --repo xiaozhou26/Chromix --ref main \
  -f build_profile=fast -f compile_jobs=auto -f build_mode=staged -f use_upstream_cache=true
gh workflow run build-macos-arm64.yml --repo xiaozhou26/Chromix --ref main \
  -f build_profile=fast -f compile_jobs=auto -f build_mode=staged -f use_upstream_cache=true
```

缓存 fetch/import 必须成功并通过对象复用校验；失败时不允许自动退回无缓存全量准备。本次不把旧152的 Chromix 检查点跨大版本迁移成154。

## 验证边界

独立固定 Chromium/V8/WebRTC 原文、core 与 macOS overlay 下的完整补丁应用及反向/正向校验可验证源码结构兼容性，不等于新的两架构编译或原生运行成功。新产物需检查实际版本、包完整性、原生启动、指纹门禁及新的像素噪声专项结果。

本次更新的是源码构建基线。已发布 macOS 下载入口和 SDK 通道暂时保持不变，直到新构建实际验收并发布；不会因此把旧包标记为新版本。
