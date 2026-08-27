"""路徑工具：開發時讀原始檔，PyInstaller 打包後讀 EXE 旁或暫存目錄。"""

from __future__ import annotations

import os
import sys


def app_dir() -> str:
    """使用者可編輯檔案（.env、token）所在目錄：開發=腳本目錄，打包=EXE 目錄。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_resource_path(relative_path: str) -> str:
    """打包神器會把圖示以 app_master_icon.ico 塞進程式內部，執行時從這裡讀。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative_path)
