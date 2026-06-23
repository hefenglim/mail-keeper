"""CSV 讀寫：分類工作表與資料夾清單。

固定欄位順序、UTF-8、含表頭、stdlib `csv` 標準跳脫。功能1 寫出工作表、
功能2 寫出資料夾清單、功能3 讀入編輯後的工作表。
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .imap_client import MailHeader

# 固定欄位順序（全英文表頭，利於 AI／試算表穩健解析）。
WORKSHEET_FIELDS = ["uid", "current_folder", "target_folder", "date", "from", "to", "subject"]
FOLDERS_FIELDS = ["folder"]
REQUIRED_FIELDS = ("uid", "current_folder", "target_folder")

# UTF-8 + BOM：讓 Microsoft Excel 直接正確判讀中文等多國語文；讀取端會剝除 BOM
# 並容忍無 BOM 的舊檔（utf-8-sig 解碼相容純 utf-8）。
CSV_ENCODING = "utf-8-sig"


class CsvError(RuntimeError):
    """CSV 讀寫或格式錯誤；由 cli 錯誤邊界轉成乾淨訊息（不崩潰）。"""


@dataclass(frozen=True)
class ClassificationRow:
    """功能3 從工作表讀入的一列分類指令。"""

    uid: str
    current_folder: str
    target_folder: str


def ensure_csv_suffix(name: str) -> str:
    """檔名沒有副檔名時補上 `.csv`；已有副檔名（含非 `.csv`）則原樣返回。

    `os.path.splitext` 具路徑感知（目錄部分的點不算副檔名）。結尾單一點（如 `report.`）
    視為無副檔名，去點後補 `.csv`。
    """
    _root, ext = os.path.splitext(name)
    if ext and ext != ".":
        return name
    return name.rstrip(".") + ".csv"


def write_worksheet(headers: Iterable[MailHeader], folder: str, path) -> None:
    """把某資料夾的郵件標題寫成分類工作表（`target_folder` 留空）。已存在則覆寫。"""
    try:
        with Path(path).open("w", encoding=CSV_ENCODING, newline="") as f:
            w = csv.writer(f)
            w.writerow(WORKSHEET_FIELDS)
            for h in headers:
                w.writerow([h.uid, folder, "", h.date, h.sender, h.recipients, h.subject])
    except OSError as exc:
        raise CsvError(f"無法寫入 CSV {path}：{exc}") from exc


def write_folders(folders: Iterable[str], path) -> None:
    """把資料夾清單寫成 CSV（本期只輸出 `folder` 欄）。已存在則覆寫。"""
    try:
        with Path(path).open("w", encoding=CSV_ENCODING, newline="") as f:
            w = csv.writer(f)
            w.writerow(FOLDERS_FIELDS)
            for name in folders:
                w.writerow([name])
    except OSError as exc:
        raise CsvError(f"無法寫入 CSV {path}：{exc}") from exc


def read_worksheet(path) -> list[ClassificationRow]:
    """讀入編輯後的工作表；依表頭定位欄位、容忍多餘欄；缺必要欄/壞檔→CsvError。"""
    try:
        text = Path(path).read_text(encoding=CSV_ENCODING)
    except OSError as exc:
        raise CsvError(f"無法讀取 CSV {path}：{exc}") from exc

    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames:
        raise CsvError(f"CSV {path} 沒有表頭或為空。")
    missing = [c for c in REQUIRED_FIELDS if c not in reader.fieldnames]
    if missing:
        raise CsvError(f"CSV {path} 缺少必要欄位：{', '.join(missing)}。")

    rows: list[ClassificationRow] = []
    for raw in reader:
        rows.append(
            ClassificationRow(
                uid=(raw.get("uid") or "").strip(),
                current_folder=(raw.get("current_folder") or "").strip(),
                target_folder=(raw.get("target_folder") or "").strip(),
            )
        )
    return rows
