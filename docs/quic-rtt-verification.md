# QUIC 初始 RTT 与固定配置验收

## 原始失败

Windows ARM64 恢复运行 `37852274649` 已完成编译及本机 PE/版本/headless 检查，但原生指纹验收在 QUIC 子项失败。原报告必须保留，不能编辑为通过后当作新证据。

报告中两个 context 都成功完成 QUIC v1 / HTTP/3 请求，既不是未采样，也不是 0-RTT 或会话恢复。实际比较的连接中，一条携带 `0x3127`，另一条没有。其余规范化 flow-control、版本、HTTP/3 SETTINGS、伪头顺序一致。原诊断将所有未识别参数的 payload hash 视为固定配置，因此拒绝了这个存在性差异。

## 精确上游语义

Chromium `154.0.8037.97` 的 [DEPS](https://chromium.googlesource.com/chromium/src/+/refs/tags/154.0.8037.97/DEPS) 将 QUICHE 固定为 `80bf9559d3a4c08dde4b85abc46d190a88ffef64`。

- [transport_parameters.cc](https://quiche.googlesource.com/quiche/+/80bf9559d3a4c08dde4b85abc46d190a88ffef64/quiche/quic/core/crypto/transport_parameters.cc) 将 `0x3127` 定义为 `kInitialRoundTripTime`。
- [transport_parameters.h](https://quiche.googlesource.com/quiche/+/80bf9559d3a4c08dde4b85abc46d190a88ffef64/quiche/quic/core/crypto/transport_parameters.h) 定义其为初始往返时延的微秒估计。
- [Chromium quic_session_pool.cc](https://chromium.googlesource.com/chromium/src/+/refs/tags/154.0.8037.97/net/quic/quic_session_pool.cc) 可以使用缓存的 SRTT 或网络估计初始化它；没有可用估计时不设置发送值。

它是已知的动态连接测量，不是保留 GREASE 参数，也不是固定 GPU/平台/浏览器版本身份。不能由这项差异推断实际浏览器身份发生变化；报告也不提供完整 RTT cache/netlog，不能断言某个值来自哪一次采样。

## 诊断修正边界

1. 解析 `0x3127` 为具名整数并标记 `dynamic_network_estimate`，严格验证 varint 长度和值范围、重复 ID、消息边界。
2. 原始观测保留存在性、长度和值；只从固定配置相等性比较中分离这个有上游依据的参数。
3. 其余未知参数的长度和 payload digest 仍严格比较，包括 `0x3128`；flow-control、版本与 GREASE 数量等也不放宽。
4. 历史报告只有 RTT payload hash 时只能核对结构，无法恢复或验证原始 wire value；新采集应记录具名值和分类，不能将历史摘要包装为新的 wire 验证。
5. 原失败报告仍为失败，修正后必须用同一成品重新执行 native smoke 和完整验收，分别记录 donor 源码及 verifier 源码。

相关测试包含真实故障派生的三个连接参数样本、合法动态值/存在性变化，以及重复参数、错误编码、流控、版本和未知参数变化的负例。

## 仅重验现有成品

`.github/workflows/reverify-win-arm64.yml` 固定已有 ARM64 成品及源码收据来源，验证外层归档摘要后在 `windows-11-arm` 上运行，**不调用 Chromium 编译**。预期主程序摘要为 `8f524a15ef3236d629a309f3df62e7fe69421e61ec36511640bb19592cbf26ed`。

该工作流不会发布产物或改写旧验收报告。只有新的全套原生验收通过后，才具备进一步评估 Release 发布的条件；`ci_gate_passed` 与 `full_acceptance` 仍分别记录。
