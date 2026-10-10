# 第三方依赖与许可

本项目的 MIT License 只适用于本项目自有代码和文档，不替代外部组件的许可。本仓库不附带 Python 运行时、Pillow 代码或二进制、Tesseract 程序、语言数据、外部 OCR 适配器。

| 组件 | 作用 | 上游许可与来源 |
| --- | --- | --- |
| Python | 运行时及标准库 | [Python 官方许可说明](https://docs.python.org/3/license.html)，包括 PSF 与历史许可 |
| Pillow | 图片处理、截图 | [Pillow LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE)，MIT-CMU；发布核查时本机 12.3.0 的许可名称一致 |
| Tesseract | 默认外部 OCR 程序 | [Tesseract LICENSE](https://github.com/tesseract-ocr/tesseract/blob/main/LICENSE)，Apache-2.0 |
| Tesseract 语言数据 | 中文、英文等模型 | [tessdata LICENSE](https://github.com/tesseract-ocr/tessdata/blob/main/LICENSE)、[tessdata_fast LICENSE](https://github.com/tesseract-ocr/tessdata_fast/blob/main/LICENSE)，Apache-2.0；以实际下载版本为准 |

这些依赖的许可没有要求本项目自有代码统一改为其许可。仅调用由用户另行安装的组件，当前未发现阻止本项目自有代码采用 MIT 的已知许可障碍。该判断基于所列组件和当前分发范围，不是完整法律意见或代码权利保证。

如未来捆绑组件、模型、安装器或复制第三方代码，应重新检查对应版本的许可、版权、NOTICE 及分发要求，保留必要说明；不能只保留本项目的 MIT 文件。自定义 OCR 由使用者提供，许可和隐私要求需另行核对。

本仓库不包含灵感截图。第三方图片的转载授权不由 MIT 许可涵盖，不能因源码开源就推定图片可公开转载。
