# 完全虚构的配套输出示例

这些文件来自同一组人工构造数据，不含真实聊天、联系人或截图：

- `sample_input.json`：12 条虚构 OCR 消息观察结果。文字、时间、页名和坐标都是示例，不是实际 OCR 采集结果。
- `sample_chat.md`：现有导出函数生成的正文，去重后共 11 条消息。
- `sample_quality_report.md`：同一次导出生成的完整质量报告。

示例故意设置以下风险，展示程序实际输出，不将风险手工改成“完美”聊天：

1. 原始第 3、4 项为跨页截断版与完整版，程序保留完整版；报告记录自动去重。
2. 成品第 6、7 条是跨发送者的疑似续句，报告提示人工核对，正文不凭语义改 sender。
3. 成品第 8 条的 00:03 偏离前后时间，正文标为时间待核对，不参与可信周期。周期为 10:00–10:12。
4. 图片、表情和通话使用占位符，不恢复图片内容或通话声音。

报告含去重前的原始项编号和成品消息编号，不能把不同编号体系当成同一个索引；保留建议请结合内容及页名核对。page_001.png 等是虚构来源标识，没有对应图片，不要把本例当作真实截图识别的准确率证明。

两个 Markdown 直接调用冻结代码的 `write_standard_outputs` 生成，没有手写风险提示。可在 Windows 工具目录运行以下代码重新生成到一个临时目录；不会采集窗口或改写示例文件：

```python
import json
import runpy
import tempfile
from pathlib import Path

root = Path.cwd()
fixture = json.loads((root / "examples/sample_input.json").read_text(encoding="utf-8"))
exporter = runpy.run_path(str(root / "wechat_chat_exporter.py"))
with tempfile.TemporaryDirectory(prefix="chat-example-") as folder:
    output = Path(folder)
    exporter["write_standard_outputs"](
        output / "sample_chat.md", None, None, None, None,
        fixture["records"], fixture["profile"],
        output / "sample_quality_report.md",
        fixture["raw_pages"], fixture["stopped_reason"],
    )
    for name in ("sample_chat.md", "sample_quality_report.md"):
        assert (output / name).read_text(encoding="utf-8") == (
            root / "examples" / name
        ).read_text(encoding="utf-8")
    print("Paired examples match the exporter.")
```

这段代码只是可复现的导出示例检查，不是完整 OCR 或全新 Windows 安装测试。
