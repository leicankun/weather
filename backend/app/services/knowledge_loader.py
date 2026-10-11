"""知识库文档加载与重叠分块器。

职责：
- 扫描 knowledge_base 目录下的 .md/.txt 文档
- ATX 标题为硬边界，段落/句子为软边界，超长元素才按字符硬切
- 返回统一的 TextChunk 结构，供 embedding 入库使用
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from app.core.config import settings

# ATX 标题允许 0～3 个前导空格、1～6 个 #，后面须为空白或行尾。
_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}(?:[ \t]+.*)?$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_PARAGRAPH_END = re.compile(r"\n[ \t]*\n(?:[ \t]*\n)*")
# 不把小数中两侧都有数字的 . 当句末；保留紧随句末的闭引号/括号。
_SENTENCE_END = re.compile(
    r"((?:[。！？!?；;]+|(?<!\d)\.+|\.+(?!\d)|…{2,})[\"'”’」』）)\]]*)[ \t]*"
)


@dataclass
class TextChunk:
    """单个文本块。"""

    title: str  # 文档标题（用于检索展示与溯源）
    source: str  # 来源文件名
    chunk_index: int  # 文档内分块序号
    content: str  # 块内容


def _extract_title(text: str, filename: str) -> str:
    """取文档首个一级标题作为标题，否则用文件名。"""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
    return Path(filename).stem


def _sections(text: str) -> list[str]:
    """正则扫描 ATX 标题；标题归入新区，前言单独保留，围栏代码中的 # 不算标题。"""
    starts = [0]
    offset = 0
    fence_char, fence_length = "", 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\n")
        fence = _FENCE.match(body)
        if fence_char:
            if (
                fence
                and fence[1][0] == fence_char
                and len(fence[1]) >= fence_length
                and not fence[2].strip()
            ):
                fence_char, fence_length = "", 0
        elif fence and not (fence[1][0] == "`" and "`" in fence[2]):
            fence_char, fence_length = fence[1][0], len(fence[1])
        elif _ATX_HEADING.match(body) and offset:
            starts.append(offset)
        offset += len(line)
    starts.append(len(text))
    return [part for a, b in pairwise(starts) if (part := text[a:b].strip())]


def _soft_boundaries(section: str) -> list[int]:
    boundaries = {0, len(section)}
    # 分隔空白的前后都可落点，不能把 300 字符短元素加上空白误判为超长。
    for match in _PARAGRAPH_END.finditer(section):
        boundaries.update((match.start(), match.end()))
    for match in _SENTENCE_END.finditer(section):
        boundaries.update((match.end(1), match.end()))
    # 标题行与正文之间也能切开，避免为了容纳标题而硬切一个本来能放下的短句。
    first_line_end = section.find("\n")
    if first_line_end >= 0 and _ATX_HEADING.match(section[:first_line_end]):
        boundaries.update((first_line_end, first_line_end + 1))
    return sorted(boundaries)


def _overlap_start(end: int, boundaries: list[int], chunk_size: int, overlap: int) -> int:
    target = max(0, end - overlap)
    index = bisect_left(boundaries, target)
    if index < len(boundaries) and boundaries[index] == target:
        return target
    # 只有目标落在原本就超过窗口的元素内部时，才允许字符级重叠。
    if boundaries[index] - boundaries[index - 1] > chunk_size:
        return target
    return min(end, boundaries[index])


def _split_section(section: str, chunk_size: int, overlap: int) -> list[str]:
    boundaries = _soft_boundaries(section)
    chunks = []
    start = covered = 0
    while covered < len(section):
        limit = min(start + chunk_size, len(section))
        end = boundaries[bisect_right(boundaries, limit) - 1]
        next_boundary = bisect_right(boundaries, covered)
        next_element_is_short = (
            boundaries[next_boundary] - boundaries[next_boundary - 1] <= chunk_size
        )
        if end <= covered and start < covered and next_element_is_short:
            # 重叠挤占了新元素的空间时，先让出重叠，不能因此硬切一个短句/短段。
            start = covered
            limit = min(start + chunk_size, len(section))
            end = boundaries[bisect_right(boundaries, limit) - 1]
        if end <= covered:
            end = limit  # 下一个不可再分的段/句超过窗口，才按字符硬切。
        if chunk := section[start:end].strip():
            chunks.append(chunk)
        covered = end
        if end == len(section):
            break
        start = _overlap_start(end, boundaries, chunk_size, overlap)
    return chunks


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """标题硬边界内按段/句回退切点；字符计长，重叠最多 overlap 且绝不跨标题。

    短段/短句保持完整；重叠以完整软元素为单位，不足空间时减少。
    超长元素内部允许硬切与字符级重叠。CRLF/CR 统一成 LF，块首尾空白去除。
    """
    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("chunk_size 须为正数，overlap 须满足 0 <= overlap < chunk_size")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return [
        chunk
        for section in _sections(text)
        for chunk in _split_section(section, chunk_size, overlap)
    ]


def load_documents(directory: str | Path | None = None) -> list[TextChunk]:
    """加载知识库目录下所有文档并分块。

    Returns:
        所有文档分块后的 TextChunk 列表。
    """
    directory = Path(directory or settings.KNOWLEDGE_DIR)
    chunk_size = settings.EMBED_CHUNK_SIZE
    overlap = settings.EMBED_CHUNK_OVERLAP

    if not directory.is_dir():
        raise FileNotFoundError(f"知识库目录不存在：{directory}")

    chunks: list[TextChunk] = []
    files = sorted(directory.glob("*"))
    for file in files:
        if not file.is_file() or file.suffix.lower() not in {".md", ".txt"}:
            continue
        text = file.read_text(encoding="utf-8")
        title = _extract_title(text, file.name)
        parts = split_text(text, chunk_size, overlap)
        for idx, part in enumerate(parts):
            chunks.append(TextChunk(title=title, source=file.name, chunk_index=idx, content=part))
    return chunks
