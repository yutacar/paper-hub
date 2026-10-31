"""Configuration loading at application entry points only."""

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from PIL import ImageFont


@dataclass(frozen=True)
class Settings:
    font_path: str
    font_path_bold: str
    tz: str = "Asia/Tokyo"
    order: str = "claude-code,codex,antigravity"
    hide: str = ""
    strata_url: str = ""
    usage_url: str = ""
    host: str = "0.0.0.0"
    port: int = 8090
    data_dir: str = "./data"

    @classmethod
    def load(cls, *, values=None, server=True):
        # Tests pass values explicitly and never touch the real configuration.
        if values is None:
            values = {**dotenv_values(".env", interpolate=False), **os.environ}
        def get(key, default=""):
            return values.get(key) or default
        required = ["FONT_PATH"] + (["STRATA_MONITOR_URL", "XANGI_USAGE_URL"] if server else [])
        missing = [key for key in required if not get(key)]
        if missing:
            raise ValueError("未設定の必須項目: " + ", ".join(missing))
        for key in ("STRATA_MONITOR_URL", "XANGI_USAGE_URL") if server else ():
            try:
                url = urlsplit(get(key))
                valid = url.scheme in {"http", "https"} and url.hostname and url.port != 0
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(key + " は有効なHTTP(S) URLを指定してください")
        try:
            port = int(get("PORT", "8090"))
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ValueError("PORT は1〜65535の整数を指定してください") from None
        tz = get("TZ", "Asia/Tokyo")
        try:
            ZoneInfo(tz)
        except (ValueError, KeyError):
            raise ValueError("TZ は有効なIANAタイムゾーン名を指定してください") from None
        for key, value in (("FONT_PATH", get("FONT_PATH")),
                           ("FONT_PATH_BOLD", get("FONT_PATH_BOLD", get("FONT_PATH")))):
            try:
                ImageFont.truetype(str(Path(value).expanduser()), 22)
            except OSError:
                raise ValueError(key + " のフォントを読み込めません") from None
        return cls(font_path=str(Path(get("FONT_PATH")).expanduser()),
                   font_path_bold=str(Path(get("FONT_PATH_BOLD", get("FONT_PATH"))).expanduser()),
                   tz=tz, order=get("USAGE_ORDER", "claude-code,codex,antigravity"),
                   hide=get("USAGE_HIDE"), strata_url=get("STRATA_MONITOR_URL"),
                   usage_url=get("XANGI_USAGE_URL"), host=get("HOST", "0.0.0.0"),
                   port=port, data_dir=str(Path(get("DATA_DIR", "./data")).expanduser()))
