# Windows ARM64 stage12 文件树增量迁移

本流程专用于用户指定的旧 216 补丁检查点迁移到当前 224 补丁源码，保留中间输出，并由 Ninja 重编受影响文件。它与仅继续旧源码的 stage13 recovery、仅重验旧成品的 native reverify 分开。

## 固定来源

- Run `37455471388`，attempt 1，job `113438156537`（stage 12）。
- Donor 源码 `15a6425cfbd7a7ecd4a69ad206d0650778bf8253`。
- Chromium `154.0.8037.97`，Windows ARM64，native profile。
- part1 artifact `11582775420`：SHA-256 `eb4f963bf75a727b90f7c3bdb85d7ec8faf99112bcbe0b9e7187885bc2b629f4`。
- part2 artifact `11582590872`：SHA-256 `2e10f4f5398ad2bb04fcd8257225d4a783177ad95767eb1e5c42a0e24adfb0ae`。

该 job 因时限耗尽失败，但快照创建及两个有效分卷上传均成功。四个上传步骤不等于必须有四个实体分卷；此检查点严格固定为以上两份 artifact。

## 验证与迁移

工作流 `.github/workflows/build-win-arm64-stage12-migrate.yml`：

1. 核对 run、job、attempt、仓库、源码提交及完整产物集合；拒绝过期、摘要不符或生产步骤失败。
2. 严格下载并核验外层 ZIP/内层分卷，检查归档路径和 ARM64 markers，执行 7-Zip 完整性测试。
3. 使用互不嵌套的 donor/target checkout 和单独工作树；只运行可信目标工具，不执行 donor 脚本。
4. 检查版本、上游 receipt、平台固定提交、GN target 与旧补丁收据；要求旧 216 个补丁字节及 selected 前缀保持一致，仅追加 `0217`–`0224`。
5. 在私有临时源码子集中执行旧栈反向/正向、追加补丁、目标 224 栈反向/正向检验；包含 `chrome/VERSION`。
6. 只写入新增补丁涉及的源文件，推进时间戳并更新准备 markers；不删除 `out/Default`、Ninja 日志或对象缓存。
7. stage13 从已迁移目录启动；后续 stage14–16 通过同一运行的检查点继续。全程需要 restored receipt，不允许冷准备或无缓存回退。
8. 成品在原生 Windows ARM64 runner 上独立验证。新的验收报告对应新构建，不沿用此前旧成品的验证结论。

迁移中断后保留 transaction blocker，必须重新解开同一已验证检查点，不能删除 blocker 强行继续。输入发生不在固定白名单内的变更时，应重新审查迁移配置，不能放宽来源检查。

## 触发

```bash
gh workflow run build-win-arm64-stage12-migrate.yml \
  --repo xiaozhou26/Chromix --ref main
```

工作流无任意 run/arch 输入，始终使用上面的固定检查点。元数据验证与迁移单元测试成功不等于真实源码迁移或原生编译成功；以云端迁移报告、Ninja 结果和新原生验收为准。

## 测试

```bash
python -m pytest \
  tools/tests/test_windows154_arm64_checkpoint.py \
  tools/tests/test_windows154_arm64_migrate_workflow.py \
  tools/tests/test_migrate_windows154_arm64_snapshot.py
```

此流程不移动既有 Release 标签，也不覆盖已经发布的同版本旧二进制。新像素噪声必须对新可执行文件执行专项诊断后才能声称已生效。
