"""导出文件生成工具：仅依赖标准库产出 XLSX / CSV 文件内容。

后端运行环境只安装了 fastapi/uvicorn/pydantic，没有 openpyxl，因此这里用
zipfile + 手写 SpreadsheetML 的方式生成最小可用的 XLSX；Excel、WPS 与
LibreOffice 均可正常打开。CSV 统一带 UTF-8 BOM，避免中文表头乱码。
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from xml.sax.saxutils import escape

# XML 1.0 不允许的控制字符（制表/换行/回车除外），写入前必须剔除
_INVALID_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    "</Types>"
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="xl/workbook.xml"/>'
    "</Relationships>"
)

_WORKBOOK = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="导出清单" sheetId="1" r:id="rId1"/></sheets>'
    "</workbook>"
)

_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
    'Target="worksheets/sheet1.xml"/>'
    "</Relationships>"
)


def _clean_text(value: object) -> str:
    """把任意单元格值转成合法的 XML 文本。"""
    if value is None:
        return ""
    return _INVALID_XML_CHARS.sub("", str(value))


def _column_ref(index: int) -> str:
    """0 基列号转 Excel 列名：0 -> A，25 -> Z，26 -> AA。"""
    ref = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        ref = chr(65 + remainder) + ref
    return ref


def _cell_xml(cell_ref: str, value: object) -> str:
    if isinstance(value, bool):
        value = "是" if value else "否"
    if isinstance(value, int | float):
        return f'<c r="{cell_ref}"><v>{value}</v></c>'
    text = escape(_clean_text(value))
    return f'<c r="{cell_ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(headers: list[str], rows: list[list[object]]) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    all_rows = [headers, *rows]
    for row_index, row in enumerate(all_rows, start=1):
        cells = [
            _cell_xml(f"{_column_ref(col_index)}{row_index}", value)
            for col_index, value in enumerate(row)
        ]
        parts.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def build_xlsx(headers: list[str], rows: list[list[object]]) -> bytes:
    """按表头与数据行生成 XLSX 字节流；没有数据行时仍保留表头。"""
    sheet = _sheet_xml(headers, rows)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        archive.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def build_csv(headers: list[str], rows: list[list[object]]) -> bytes:
    """生成带 UTF-8 BOM 的 CSV 字节流，空结果同样只带表头。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(["" if value is None else str(value) for value in row])
    return b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
