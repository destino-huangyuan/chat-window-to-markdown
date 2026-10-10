# OCR 接入说明

本项目默认使用外部安装的 Tesseract OCR，不把 OCR 引擎打包进仓库。

## 默认方案：Tesseract

程序会按顺序查找：

1. 命令行 `PATH` 中的 `tesseract`
2. `C:\Program Files\Tesseract-OCR\tesseract.exe`
3. `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`
4. 当前用户目录下常见的 Tesseract 安装位置

如果 Tesseract 安装在其它位置，可以手动指定：

```bat
开始导出微信聊天.bat --tesseract-path "D:\Tools\Tesseract-OCR\tesseract.exe"
```

默认语言是：

```text
chi_sim+eng
```

可以改成其它语言：

```bat
开始导出微信聊天.bat --tesseract-lang chi_sim+eng+jpn
```

前提是对应语言包已经安装到 Tesseract 的 `tessdata` 目录。

## 自定义 OCR 引擎接口

如果要接入 PaddleOCR、EasyOCR、云 OCR 或其它识别库，推荐写一个外部适配脚本，然后通过 `--ocr-engine custom` 接入。

当前自定义接口要求：

- 输入：一张图片路径
- 输出：向 stdout 输出 Tesseract TSV 格式
- 编码：UTF-8
- 输入图片是工具预处理后的图片；返回坐标必须对应该输入图片，不能改用原截图坐标。

命令模板：

```bat
开始导出微信聊天.bat --ocr-engine custom --custom-ocr-tsv-command "python my_ocr_adapter.py --image {imageq} --lang {lang} --mode tsv"
```

可用占位：

- `{image}`：图片路径，不额外加引号
- `{imageq}`：已加命令行引号的图片路径，Windows 下推荐使用
- `{lang}`：当前语言参数，例如 `chi_sim+eng`
- `{psm}`：识别模式提示，兼容 Tesseract 的 psm 概念
- `{mode}`：`tsv` 或 `text`

## 可选：短文本二次识别

工具对少数字泡会做局部二次识别。自定义 OCR 可以额外提供纯文本命令：

```bat
开始导出微信聊天.bat ^
  --ocr-engine custom ^
  --custom-ocr-tsv-command "python my_ocr_adapter.py --image {imageq} --lang {lang} --mode tsv" ^
  --custom-ocr-text-command "python my_ocr_adapter.py --image {imageq} --lang {lang} --mode text"
```

如果不提供 `--custom-ocr-text-command`，主 OCR 流程仍可运行，只是跳过短文本二次识别。

## TSV 输出格式

TSV 至少应包含 Tesseract 常用列：

```text
level	page_num	block_num	par_num	line_num	word_num	left	top	width	height	conf	text
```

其中：

- `left/top/width/height` 用于判断文字位置、左右发送者和时间。
- `conf` 用于过滤低置信度噪声，必须使用 **0–100** 的分数。若外部引擎返回 0–1 的概率，适配器应先乘以 100，例如 `0.95` 输出为 `95`；工具不会自动换算。
- `text` 是识别出的文字。

正文默认过滤低于 `18` 的有效置信度（可通过 `--min-conf` 调整）；时间识别单独过滤低于 `35` 的有效置信度。不换算的 `0.95` 会被当成极低置信度，可能导致文字和时间全部丢失。没有置信度时可用 `-1` 表示未知；它不会被当前置信度阈值过滤，但不要用它掩盖已知低分。

当前解析器只读取 `level=5` 的记录。适配器必须把文字输出为 word 记录，按行填写 `block_num/par_num/line_num`，即使引擎本身返回整行文字也应映射为 `level=5`。不能直接输出 `level=4` 的 line 记录，否则主流程会忽略它们。

下面的独立 Python 示例会向 stdout 输出带表头和真实制表符的 TSV，并演示 0–1 分数换算。它仅演示格式，不是完整 OCR 适配器；实际接入时请替换文字、坐标和分数，确保与输入图片对应。

```python
import csv
import sys

sys.stdout.reconfigure(encoding="utf-8")
writer = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
writer.writerow([
    "level", "page_num", "block_num", "par_num", "line_num", "word_num",
    "left", "top", "width", "height", "conf", "text",
])
engine_score = 0.95
writer.writerow([5, 1, 1, 1, 1, 1, 300, 100, 100, 30,
                 engine_score * 100, "示例文字"])
```

不要把反斜杠加字母 t 的字面文本当作 TSV 分隔符；应像示例一样由 `csv.writer` 生成实际制表符。

脚本日志应写入 stderr，不要混入 stdout 的 TSV。外部脚本与依赖由使用者自行安装；本仓库没有附带适配器或模型。运行自定义命令等同于执行外部程序，只接入可信脚本。若脚本调用云服务，图片可能被上传，不再符合默认本地流程的隐私边界。

## 设计边界

本项目只负责截图、滚动、消息结构化、清洗和 Markdown 导出。

OCR 引擎本身作为外部依赖存在，用户可以：

- 使用默认 Tesseract
- 指定自己的 Tesseract 路径
- 用自定义命令接入其它 OCR
