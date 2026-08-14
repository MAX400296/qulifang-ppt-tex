#!/usr/bin/env python3
"""在 macOS 上调用 Microsoft PowerPoint 原生导出 PDF，避免字体与公式替换。"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


POWERPOINT_APP = Path("/Applications/Microsoft PowerPoint.app")


def available() -> bool:
    return sys.platform == "darwin" and POWERPOINT_APP.is_dir() and shutil.which("osascript") is not None


def export_pdf(source: Path, output: Path, replace_existing: bool) -> dict[str, object]:
    if not available():
        raise RuntimeError("需要 macOS、Microsoft PowerPoint 和 osascript")
    if not source.is_file() or source.suffix.lower() != ".pptx":
        raise RuntimeError(f"请输入存在的 .pptx 文件：{source}")
    if output.suffix.lower() != ".pdf":
        raise RuntimeError("输出文件必须使用 .pdf 扩展名")
    if output.exists() and not replace_existing:
        raise RuntimeError(f"输出已存在：{output}；确认后使用 --replace-existing")
    output.parent.mkdir(parents=True, exist_ok=True)
    script = """
on run argv
    set inputFile to POSIX file (item 1 of argv)
    set outputFile to POSIX file (item 2 of argv)
    tell application "Microsoft PowerPoint"
        open inputFile
        set openedPresentation to active presentation
        save openedPresentation in outputFile as save as PDF
        close openedPresentation saving no
    end tell
end run
"""
    completed = subprocess.run(
        ["osascript", "-e", script, str(source), str(output)],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise RuntimeError(f"PowerPoint 原生 PDF 导出失败：{message}")
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("PowerPoint 未生成有效 PDF")
    return {
        "ok": True,
        "renderer": "microsoft-powerpoint-native-pdf",
        "source": str(source),
        "output": str(output),
        "sizeBytes": output.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 Microsoft PowerPoint 原生导出 PDF")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        print(
            json.dumps(
                {
                    "available": available(),
                    "platform": sys.platform,
                    "powerpoint": str(POWERPOINT_APP) if POWERPOINT_APP.is_dir() else None,
                    "osascript": shutil.which("osascript"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.input is None or args.output is None:
        parser.error("除 --check 外，必须同时提供 --input 和 --output")
    try:
        result = export_pdf(
            args.input.expanduser().resolve(),
            args.output.expanduser().resolve(),
            args.replace_existing,
        )
    except RuntimeError as exc:
        parser.exit(1, f"错误：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
