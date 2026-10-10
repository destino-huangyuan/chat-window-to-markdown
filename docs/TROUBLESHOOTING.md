# 安装与运行排查

## Python 命令不存在或只打开应用商店

先按 README 的官方入口安装 Python 运行时，再重新打开命令提示符，检查 `py -3 --version`；没有 py 时检查 `python --version`。不能把存在命令名等同于有可用运行时。

批处理有 py 时优先用 `py -3`。如果 py 存在但提示没有安装 Python，先修复其运行时配置；当前批处理不会在这种情况下自动切换到另一环境。

## No module named PIL / Pillow: missing

双击 `install_dependencies.bat`。它在工具目录中选择与启动入口相同的解释器安装 Pillow。若要手动安装，在工具目录的命令提示符中运行：

```bat
py -3 -m pip install -r requirements.txt
```

没有 py 时使用 `python -m pip install -r requirements.txt`。不要只调用不明来源的 pip。安装后重新运行 `diagnose_ocr.bat`，确认 `Pillow: OK`。

## pip 下载失败、权限错误或版本不兼容

保留错误提示，确认联网、Python 版本和写入权限。工具目录放在自己可写的文件夹，不要从 ZIP 中运行。若为网络问题，可选择你信任的包镜像，使用同一解释器，例如：

```bat
py -3 -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

镜像是外部服务，非本项目保证可用的安装渠道。若显示 `No module named pip`，按 Python 官方说明修复 pip；错误未解决前不要启动导出。不要为解决报错下载来源不明的 DLL 或批处理。

## Tesseract was not found

按 README 的 Tesseract Windows 指引安装。安装在常见目录之外时，在工具目录的命令提示符中指定：

```bat
开始导出微信聊天.bat --tesseract-path "D:\Tools\Tesseract-OCR\tesseract.exe"
```

诊断脚本只检查 PATH 和常见默认位置，不读取这条启动命令的参数。可用你的实际 tesseract.exe 路径执行 `--list-langs` 自查。

## chi_sim 或 eng 不存在 / Error opening data file

默认语言组合为 `chi_sim+eng`。确认实际 Tesseract 的 tessdata 目录包含这两份 `.traineddata` 文件，再检查 `--list-langs` 中是否分别列出 chi_sim 和 eng。

缺失时按 README 的官方语言数据入口补齐。不要将下载网页或 ZIP 重命名为语言文件；确保文件与引擎兼容。自定义语言参数只适用于已安装的对应语言，详见 OCR_ADAPTER.md。

## 诊断结束了，但仍不能运行

诊断不是安装器，也不验证聊天界面适配。当前脚本缺少依赖时仍可能返回退出码 0，应看实际提示，不要只看窗口是否正常关闭。

## 批处理出现“不是内部或外部命令”或乱码

使用完整解压后的发布文件，不要从聊天消息手工拼接批处理。编辑 .bat 时保留 UTF-8 编码和 CRLF 换行，避免截断长命令。仓库的 .gitattributes 用于保持检出后的换行。

## 导出包含其他窗口或文件名

按回车开始前，确保运行窗口与其他窗口不遮挡目标聊天。不要在采集期间移动、缩放、最小化或切换目标窗口。

微信左侧会话列表有自动裁剪；特殊布局或尝试其他应用时可加 `--no-auto-chat-panel`，并按 README 调整边距。这不表示其他应用已完整适配。

## 导出提前停止 / 结果目录为空

工具会在窗口关闭、连续无新增或连续截图不变时停止，避免无限采集。`打开导出结果.bat` 只打开目录；尚未运行导出或启动前报错时，目录可能为空。查看运行窗口中的停止或报错提示。

## 头像归属或对象名沿用上次配置

本地 config.json 保存对象名和左右身份。换聊天时显式提供 `--target-name`、`--self-position`，或删除该配置后重新确认。群聊不能可靠区分每位左侧成员。

## 如何打开 Markdown / 找不到报告引用的原图

用记事本打开 .md 即可阅读；已有 Markdown 工具也可预览。报告的 page_001.png 等是页名，原图在本次 `work/wechat_chat_exporter/运行时间/screenshots/` 中。

正常结束默认只保留前 300 页原图。需要更多页作人工核对时，在采集前加 `--keep-screenshots`。已经清理的截图不能仅凭报告恢复。

## 重复、漏字、漏行或时间待核对

先核对原图是否包含完整气泡，再复核发送者、顺序和时间。报告可能漏报或误报，不能凭语义自行补写原文。中文里的英文简称也可能被保守规则提示，不等于确认 OCR 错误。
