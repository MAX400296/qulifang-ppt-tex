#!/usr/bin/env python3
"""在处理 PPT 前统一检查依赖，并为缺失项生成安装建议。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple


MINIMUM_PYTHON = (3, 10)
POWERPOINT_APP = Path("/Applications/Microsoft PowerPoint.app")


def _first_command(
    which: Callable[[str], Optional[str]], names: Iterable[str]
) -> Optional[str]:
    for name in names:
        candidate = which(name)
        if candidate:
            return candidate
    return None


def _find_soffice(
    which: Callable[[str], Optional[str]],
    platform_name: str,
    static_candidates: Optional[Iterable[Path]] = None,
) -> Optional[str]:
    command = _first_command(which, ("soffice", "libreoffice"))
    if command:
        return command
    candidates = list(static_candidates or ())
    if static_candidates is None and platform_name == "darwin":
        candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    return next((str(path) for path in candidates if path.is_file()), None)


def _dependency_roots(
    skill_dir: Path, home_dir: Path, codex_home: Optional[Path]
) -> List[Path]:
    candidates = [skill_dir.parent / "qulifang-to-tex"]
    if codex_home:
        candidates.append(codex_home / "skills" / "qulifang-to-tex")
    candidates.extend(
        [
            home_dir / ".codex" / "skills" / "qulifang-to-tex",
            home_dir / ".agents" / "skills" / "qulifang-to-tex",
        ]
    )
    unique: List[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _find_tex_skill(
    skill_dir: Path, home_dir: Path, codex_home: Optional[Path]
) -> Optional[Path]:
    for candidate in _dependency_roots(skill_dir, home_dir, codex_home):
        builder = candidate / "scripts" / "build_package.py"
        validator = candidate / "scripts" / "validate_package.py"
        if builder.is_file() and validator.is_file():
            return candidate
    return None


def _component(
    component_id: str,
    label: str,
    *,
    required: bool,
    available: bool,
    detected: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, object]:
    if available:
        status = "ok"
    elif required:
        status = "missing"
    else:
        status = "optional-missing"
    return {
        "id": component_id,
        "label": label,
        "required": required,
        "available": available,
        "status": status,
        "detected": detected,
        "note": note,
    }


def _install_commands(component_id: str, platform_name: str) -> Tuple[List[str], str]:
    if component_id == "python":
        if platform_name == "darwin":
            return ["brew install python@3.12"], "安装 Python 3.10 或更高版本后重新运行预检。"
        if platform_name.startswith("linux"):
            return ["sudo apt-get update", "sudo apt-get install -y python3 python3-pip"], "确保 python3 版本不低于 3.10。"
        return [], "请从 https://www.python.org/downloads/ 安装 Python 3.10 或更高版本。"
    if component_id == "pillow":
        return [f'"{sys.executable}" -m pip install --user Pillow'], "Pillow 用于读取、裁切和验证页面图片。"
    if component_id == "pdftoppm":
        if platform_name == "darwin":
            return ["brew install poppler"], "pdftoppm 由 Poppler 提供。"
        if platform_name.startswith("linux"):
            return ["sudo apt-get update", "sudo apt-get install -y poppler-utils"], "pdftoppm 由 poppler-utils 提供。"
        return [], "请安装 Poppler，并确保 pdftoppm 已加入 PATH。"
    if component_id == "renderer":
        if platform_name == "darwin":
            return ["brew install --cask libreoffice"], "也可以安装 Microsoft PowerPoint；Skill 会优先使用 PowerPoint。"
        if platform_name.startswith("linux"):
            return ["sudo apt-get update", "sudo apt-get install -y libreoffice"], "LibreOffice 是非 macOS 环境的默认渲染器。"
        return [], "请安装 LibreOffice，并确保 soffice 已加入 PATH。"
    if component_id == "powerpoint":
        return [], "请安装 Microsoft PowerPoint；macOS 原生导出还需要系统自带的 osascript。"
    if component_id == "libreoffice":
        if platform_name == "darwin":
            return ["brew install --cask libreoffice"], "安装后应能找到 soffice。"
        if platform_name.startswith("linux"):
            return ["sudo apt-get update", "sudo apt-get install -y libreoffice"], "安装后应能找到 soffice。"
        return [], "请安装 LibreOffice，并确保 soffice 已加入 PATH。"
    if component_id == "qulifang-to-tex":
        return [
            "python3 ~/.codex/skills/.system/skill-installer/scripts/"
            "install-skill-from-github.py --repo MAX400296/qulifang-to-tex "
            "--path skills/qulifang-to-tex"
        ], "该 sibling Skill 负责生成并校验题目 TEX 包。"
    if component_id == "external-pages":
        return [], "请提供包含逐页 PNG/JPG 的有效目录，或改用 auto 渲染模式。"
    return [], "请安装该组件后重新运行预检。"


def check_environment(
    *,
    renderer: str = "auto",
    external_pages: Optional[Path] = None,
    skill_dir: Optional[Path] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
    platform_name: str = sys.platform,
    python_version: Optional[Tuple[int, ...]] = None,
    pillow_available: Optional[bool] = None,
    powerpoint_app: Path = POWERPOINT_APP,
    static_soffice_candidates: Optional[Iterable[Path]] = None,
    home_dir: Optional[Path] = None,
    codex_home: Optional[Path] = None,
) -> Dict[str, object]:
    """返回机器可读的依赖报告；所有 required 缺失项都会阻断执行。"""

    if renderer not in {"auto", "powerpoint", "libreoffice", "external"}:
        raise ValueError(f"不支持的 renderer：{renderer}")

    skill_root = (skill_dir or Path(__file__).resolve().parents[1]).expanduser().resolve()
    current_home = (home_dir or Path.home()).expanduser().resolve()
    configured_codex_home = codex_home
    if configured_codex_home is None and os.environ.get("CODEX_HOME"):
        configured_codex_home = Path(os.environ["CODEX_HOME"])
    if configured_codex_home:
        configured_codex_home = configured_codex_home.expanduser().resolve()

    version = tuple(python_version or tuple(sys.version_info[:3]))
    python_ok = version >= MINIMUM_PYTHON
    if pillow_available is None:
        pillow_available = importlib.util.find_spec("PIL") is not None

    osascript = which("osascript")
    powerpoint_ok = platform_name == "darwin" and powerpoint_app.is_dir() and bool(osascript)
    soffice = _find_soffice(which, platform_name, static_soffice_candidates)
    pdftoppm = which("pdftoppm")
    tex_skill = _find_tex_skill(skill_root, current_home, configured_codex_home)

    selected_renderer: Optional[str] = None
    if renderer == "auto":
        if powerpoint_ok:
            selected_renderer = "microsoft-powerpoint"
        elif soffice:
            selected_renderer = "libreoffice"
    elif renderer == "powerpoint" and powerpoint_ok:
        selected_renderer = "microsoft-powerpoint"
    elif renderer == "libreoffice" and soffice:
        selected_renderer = "libreoffice"

    external_ready = False
    if renderer == "external" and external_pages and external_pages.is_dir():
        external_ready = any(
            path.suffix.lower() in {".png", ".jpg", ".jpeg"}
            for path in external_pages.iterdir()
            if path.is_file()
        )
        if external_ready:
            selected_renderer = "external-pages"

    checks = [
        _component(
            "python",
            "Python >= 3.10",
            required=True,
            available=python_ok,
            detected=".".join(str(value) for value in version),
        ),
        _component(
            "pillow",
            "Python Pillow",
            required=True,
            available=bool(pillow_available),
            detected="PIL" if pillow_available else None,
        ),
        _component(
            "qulifang-to-tex",
            "qulifang-to-tex Skill",
            required=True,
            available=tex_skill is not None,
            detected=str(tex_skill) if tex_skill else None,
        ),
    ]

    if renderer == "auto":
        checks.extend(
            [
                _component(
                    "renderer",
                    "PowerPoint 或 LibreOffice 渲染器",
                    required=True,
                    available=selected_renderer is not None,
                    detected=selected_renderer,
                ),
                _component(
                    "powerpoint",
                    "Microsoft PowerPoint 原生导出",
                    required=False,
                    available=powerpoint_ok,
                    detected=str(powerpoint_app) if powerpoint_ok else None,
                    note="首选渲染器；缺失时可使用 LibreOffice。",
                ),
                _component(
                    "libreoffice",
                    "LibreOffice/soffice",
                    required=False,
                    available=bool(soffice),
                    detected=soffice,
                    note="PowerPoint 不可用时的备用渲染器。",
                ),
            ]
        )
    elif renderer == "powerpoint":
        checks.append(
            _component(
                "powerpoint",
                "Microsoft PowerPoint + osascript",
                required=True,
                available=powerpoint_ok,
                detected=str(powerpoint_app) if powerpoint_ok else None,
            )
        )
    elif renderer == "libreoffice":
        checks.append(
            _component(
                "libreoffice",
                "LibreOffice/soffice",
                required=True,
                available=bool(soffice),
                detected=soffice,
            )
        )
    else:
        checks.append(
            _component(
                "external-pages",
                "外部逐页高清图片",
                required=True,
                available=external_ready,
                detected=str(external_pages.resolve()) if external_ready and external_pages else None,
            )
        )

    if renderer != "external":
        checks.append(
            _component(
                "pdftoppm",
                "Poppler/pdftoppm",
                required=True,
                available=bool(pdftoppm),
                detected=pdftoppm,
            )
        )

    xelatex = _first_command(which, ("xelatex", "latexmk"))
    checks.append(
        _component(
            "xelatex",
            "XeLaTeX 预览工具",
            required=False,
            available=bool(xelatex),
            detected=xelatex,
            note="仅生成 PDF 预览时需要，不影响课件 ZIP。",
        )
    )

    blocking = [str(item["id"]) for item in checks if item["required"] and not item["available"]]
    install_plan = []
    for component_id in blocking:
        commands, note = _install_commands(component_id, platform_name)
        install_plan.append({"id": component_id, "commands": commands, "note": note})

    return {
        "protocol": "qulifang-ppt-tex-preflight",
        "version": 1,
        "ok": not blocking,
        "platform": platform_name,
        "rendererMode": renderer,
        "selectedRenderer": selected_renderer,
        "blockingMissing": blocking,
        "checks": checks,
        "installPlan": install_plan,
    }


def format_human_report(report: Dict[str, object], rerun_command: str) -> str:
    lines = ["qulifang-ppt-tex 运行前依赖检查", ""]
    for item in report["checks"]:
        if item["available"]:
            marker = "✅"
        elif item["required"]:
            marker = "❌"
        else:
            marker = "○"
        detail = f"：{item['detected']}" if item.get("detected") else ""
        suffix = "（可选）" if not item["required"] else ""
        lines.append(f"{marker} {item['label']}{suffix}{detail}")
        if item.get("note") and not item["available"]:
            lines.append(f"   {item['note']}")

    lines.append("")
    if report["ok"]:
        lines.append(f"结论：依赖满足，可以开始处理 PPT；渲染器：{report['selectedRenderer']}。")
        return "\n".join(lines)

    lines.append("结论：依赖未满足，已阻止 PPT 处理。")
    lines.append("")
    lines.append("安装建议（执行前先征得用户同意）：")
    for index, item in enumerate(report["installPlan"], 1):
        lines.append(f"{index}. {item['id']}：{item['note']}")
        commands = item.get("commands") or []
        for command in commands:
            lines.append(f"   {command}")
    lines.extend(["", "安装完成后重新运行：", f"   {rerun_command}"])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 qulifang-ppt-tex 运行依赖")
    parser.add_argument(
        "--renderer",
        choices=("auto", "powerpoint", "libreoffice", "external"),
        default="auto",
        help="选择渲染依赖；默认优先 PowerPoint，其次 LibreOffice",
    )
    parser.add_argument("--external-pages", type=Path, help="external 模式的逐页 PNG/JPG 目录")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()

    report = check_environment(renderer=args.renderer, external_pages=args.external_pages)
    rerun = f'python3 "{Path(__file__).resolve()}" --renderer {args.renderer}'
    if args.external_pages:
        rerun += f' --external-pages "{args.external_pages.expanduser().resolve()}"'
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_human_report(report, rerun))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
