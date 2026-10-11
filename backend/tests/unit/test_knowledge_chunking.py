"""文档分块的标题隔离、软边界、硬切及重叠契约。"""

from random import Random

import pytest

from app.services.knowledge_loader import load_documents, split_text


@pytest.mark.parametrize("level", range(1, 7))
def test_atx标题即使全文不到窗口也隔离(level):
    heading = "#" * level
    assert split_text(f"前言\n{heading} 第一节\n甲。\n{heading} 第二节\n乙。", 300, 50) == [
        "前言",
        f"{heading} 第一节\n甲。",
        f"{heading} 第二节\n乙。",
    ]


def test_相邻标题与无正文标题不丢失():
    assert split_text("#\n## 二\n### 三\n正文", 300, 50) == ["#", "## 二", "### 三\n正文"]


@pytest.mark.parametrize(
    "false_heading", ["####### 七", "#标签", r"\# 转义", "正文 # 内联", "    # 缩进", "标题\n==="]
)
def test_非atx不产生硬边界(false_heading):
    text = f"前言\n{false_heading}\n正文"
    assert split_text(text, 300, 50) == [text]


def test_缩进空白和关闭井号支持():
    assert split_text("前言\n   ##\t标题 ##\n正文", 300, 50) == ["前言", "##\t标题 ##\n正文"]


@pytest.mark.parametrize("fence", ["```python", "~~~~text"])
def test_围栏代码内部标题不分区(fence):
    closing = fence[:3] if fence.startswith("`") else "~~~~"
    section = f"# 第一节\n{fence}\n## 代码里的标题\n{closing}\n正文"
    assert split_text(f"{section}\n# 第二节\n乙", 300, 50) == [section, "# 第二节\n乙"]


def test_较短围栏不能结束代码块():
    text = "# 甲\n````\n```\n# 仍为代码\n````\n# 乙\n正文"
    assert split_text(text, 300, 50) == ["# 甲\n````\n```\n# 仍为代码\n````", "# 乙\n正文"]


def test_换行标准化():
    assert split_text("# 甲\r\n正文\r## 乙\r\n正文", 300, 50) == ["# 甲\n正文", "## 乙\n正文"]


def test_窗口在段落中间回退到前段结尾():
    assert split_text("甲" * 200 + "\n\n" + "乙" * 200, 300, 50) == ["甲" * 200, "乙" * 200]


def test_空行不把满窗口短段误判为超长():
    assert split_text("甲" * 300 + "\n\n" + "乙" * 200, 300, 50) == ["甲" * 300, "乙" * 200]


def test_句间空白不导致满窗口短句被硬切():
    first, second = "甲" * 299 + "。", "乙" * 299 + "。"
    assert split_text(first + "    " + second, 300, 50) == [first, second]


@pytest.mark.parametrize(
    "punctuation", ["。", "！", "？", ".", "!", "?", "；", ";", "。”", "？）", "……"]
)
def test_长段包含短句时回退句末而不硬切(punctuation):
    first, second = "甲" * 180 + punctuation, "乙" * 180 + punctuation
    assert split_text(first + second, 300, 50) == [first, second]


def test_小数点不作为句末():
    first, second = "甲" * 180 + "。", "乙" * 110 + "3.14" + "丙" * 80 + "。"
    assert split_text(first + second, 300, 50) == [first, second]


def test_短句重叠保留完整元素最多50字符():
    sentences = [str(i) * 49 + "。" for i in range(7)]
    assert split_text("".join(sentences), 300, 50) == [
        "".join(sentences[:6]),
        "".join(sentences[5:]),
    ]


def test_软重叠不为凑50字符而切短句():
    sentences = ["甲" * 79 + "。", "乙" * 179 + "。", "丙" * 99 + "。"]
    assert split_text("".join(sentences), 300, 50) == ["".join(sentences[:2]), sentences[2]]


def test_重叠挤占空间时让出空间保持短句完整():
    sentences = ["甲" * 249 + "。", "乙" * 49 + "。", "丙" * 279 + "。"]
    assert split_text("".join(sentences), 300, 50) == ["".join(sentences[:2]), sentences[2]]


def test_标题挤占空间不能硬切能放下的句子():
    sentence = "甲" * 299 + "。"
    assert split_text("# 标题\n" + sentence, 300, 50) == ["# 标题", sentence]


def test_超长元素硬切300并重叠50不输出已覆盖尾巴():
    text = "".join(chr(0x4E00 + i) for i in range(700))
    assert split_text(text, 300, 50) == [text[:300], text[250:550], text[500:]]


def test_超长元素后接短句仍完整():
    long = "甲" * 649 + "。"
    short = "乙" * 279 + "。"
    assert split_text(long + short, 300, 50) == [long[:300], long[250:550], long[500:], short]


def test_标题处无重叠泄漏():
    first = "# A\n" + "甲" * 700
    second = "## B\n" + "乙" * 350
    chunks = split_text(first + "\n" + second, 300, 50)
    assert all(len(chunk) <= 300 for chunk in chunks)
    assert all(not ("甲" in chunk and "乙" in chunk) for chunk in chunks)
    index = next(i for i, chunk in enumerate(chunks) if chunk.startswith("## B"))
    assert all("乙" not in chunk for chunk in chunks[:index])
    assert all("甲" not in chunk for chunk in chunks[index:])


def test_随机长短混合句覆盖有序且块长度受限():
    random = Random(1011)
    for _ in range(30):
        sentences = []
        codepoint = 0x4E00
        for _ in range(8):
            length = random.randint(1, 650)
            sentences.append("".join(chr(i) for i in range(codepoint, codepoint + length)) + "。")
            codepoint += length
        original = "".join(sentences)
        chunks = split_text(original, 300, 50)
        reconstructed = chunks[0]
        for chunk in chunks[1:]:
            overlaps = [
                n
                for n in range(1, min(50, len(chunk), len(reconstructed)) + 1)
                if reconstructed.endswith(chunk[:n])
            ]
            reconstructed += chunk[max(overlaps, default=0) :]
        assert reconstructed == original
        assert all(0 < len(chunk) <= 300 for chunk in chunks)
        # 原本能装下的整句应完整存在于某个块中。
        assert all(
            any(sentence in chunk for chunk in chunks)
            for sentence in sentences
            if len(sentence) <= 300
        )


@pytest.mark.parametrize("size, overlap", [(0, 0), (-1, 0), (300, -1), (300, 300), (300, 301)])
def test_非法窗口或重叠拒绝(size, overlap):
    with pytest.raises(ValueError):
        split_text("正文", size, overlap)


def test_空白与零重叠():
    assert split_text(" \r\n\t ", 300, 50) == []
    assert split_text("甲" * 600, 300, 0) == ["甲" * 300, "甲" * 300]


def test_公共加载器共享标题规则保留来源序号(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "EMBED_CHUNK_SIZE", 300)
    monkeypatch.setattr(settings, "EMBED_CHUNK_OVERLAP", 50)
    (tmp_path / "guide.md").write_text("# 指南\n甲。\n## 第二节\n乙。", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("丙。", encoding="utf-8")
    (tmp_path / "ignore.json").write_text("{}", encoding="utf-8")
    chunks = load_documents(tmp_path)
    assert [(c.source, c.title, c.chunk_index, c.content) for c in chunks] == [
        ("guide.md", "指南", 0, "# 指南\n甲。"),
        ("guide.md", "指南", 1, "## 第二节\n乙。"),
        ("notes.txt", "notes", 0, "丙。"),
    ]
