# v1.0 发布文件与隐私检查

此清单供维护者在每次提交、推送或构建分享包前使用。功能版本 v1.0 Verified 已冻结；发布准备不修改 OCR、采集、去重或消息解析。

Verified 是已验证 Windows 11、微信私聊样本的验收说明，不是无错漏、全平台或全新电脑安装验证承诺。私人验收素材不分发，不应为了公开复现而恢复真实聊天。

## 批准的发布文件

```text
.gitattributes
.gitignore
LICENSE
README.md
diagnose_ocr.bat
docs/GIT_RELEASE_CHECKLIST.md
docs/OCR_ADAPTER.md
docs/PRIVACY.md
docs/THIRD_PARTY.md
docs/TROUBLESHOOTING.md
examples/README.md
examples/sample_chat.md
examples/sample_input.json
examples/sample_quality_report.md
install_dependencies.bat
requirements.txt
wechat_chat_exporter.py
windows_ocr.ps1
使用说明.txt
导出结果/.gitkeep
开始导出微信聊天.bat
打开导出结果.bat
```

只从独立工具目录操作，不上传其父工作目录。灵感截图、Release 草稿、评审报告和本地确认材料不在本清单中。

## 依赖与许可

- 本项目自有代码、文档采用 MIT License，版权署名：荒原。
- requirements.txt 仅安装 Pillow。Tesseract、语言数据、Python 运行时和外部适配器由使用者提供。
- 外部组件保留各自许可，详见 THIRD_PARTY.md。本项目 MIT 不授权第三方图片转载。
- .gitattributes 保持 .bat 和 .ps1 的 CRLF；不要手工拼接或截断批处理。

## 提交前检查

在独立项目目录检查以下信息，确认没有来自父目录的仓库或其他工作区：

```bat
git rev-parse --show-toplevel
git status --short --untracked-files=all
git ls-files
git ls-files -ci --exclude-standard
git diff --cached --name-only
git diff --cached
git diff --cached --check
```

首次初始化、暂存、提交和公开发布是不同动作。初始化和暂存不等于批准推送。暂存文件应与上面的清单一致；不使用 git add -f 绕过白名单。

## 隐私核查

- [ ] 暂存正文只包含工具、文档和完全虚构示例。
- [ ] 没有真实聊天、联系人、截图、私人配置、日志、缓存、备份或绝对用户目录。
- [ ] config.json、work、tests、导出结果中的实际记录、.env、截图和压缩包被忽略。
- [ ] 未意外提交许可证草稿、Release 本地预览或灵感截图。
- [ ] 许可证、署名与任何第三方分发说明完整。
- [ ] 本次新增文件都经过人工阅读，不能仅依赖关键词扫描。

忽略规则不会移除已跟踪文件或历史内容。已有仓库还应检查全部分支、标签和历史：

```bat
git log --all --name-only --format=
```

发现敏感历史时先停止发布，另行处理，不要直接推送。尚无提交的仓库没有可审查历史，不代表完成过历史清理。

## 分享包与 Release

分享 ZIP 时只从批准文件清单构建，不压缩整个运行目录。压缩工具和网页上传不会自动遵循 .gitignore。

灵感截图只有在明确确认文件、公开来源与转载权限后才能公开。若确实需要进入仓库，必须先另行批准路径、更新白名单并复查，不把一般图片过滤规则整体放开。

README 不包含个人故事。Release 前言与结尾按作者确认文案发布，图片不是工具运行或准确率示例。仓库名、README 链接与图片公开链接需在正式发布前确认。

没有作者明确批准，不创建公开仓库、不推送、不打正式标签、不发布 Release。全新 Windows 完整安装路径尚未实测的事实应保留在已知限制中，不隐瞒，也不作为已完成测试报告。
