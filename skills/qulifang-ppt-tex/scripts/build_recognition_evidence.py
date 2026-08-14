#!/usr/bin/env python3
"""从 prepare_pptx 产物生成逐文本框、可按字符区间引用的识别证据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


EXCLUDED_NAME_TOKENS = {
    "background",
    "footer",
    "header",
    "logo",
    "page-number",
    "question-label",
    "slide-subtitle",
    "slide-title",
}
PROMPT_RE = re.compile(
    r"(求|证明|计算|判断|选择|填空|写出|说明|解答|多少|为何|为什么|是否|____|_{4,}|[？?])"
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 顶层必须是对象")
    return value


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_name(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def excluded_shape(shape: dict[str, Any], slide_cy: int) -> bool:
    name = normalize_name(shape.get("name"))
    if any(token in name for token in EXCLUDED_NAME_TOKENS):
        return True
    box = shape.get("boxEmu")
    if isinstance(box, dict):
        try:
            y = int(box.get("y") or 0)
            cy = int(box.get("cy") or 0)
        except (TypeError, ValueError):
            return False
        if y >= slide_cy * 0.89 or y + cy <= slide_cy * 0.035:
            return True
    return False


def role_for(shape: dict[str, Any], text: str) -> str:
    name = normalize_name(shape.get("name"))
    compact = "".join(text.split())
    if "question-text" in name or "problem-text" in name or "exercise-text" in name:
        return "question_text_candidate"
    if len(compact) >= 18 or PROMPT_RE.search(text):
        return "question_text_candidate"
    if len(compact) <= 16:
        return "short_text_or_diagram_label"
    return "supporting_text"


def build(work: Path) -> dict[str, Any]:
    source_info = read_json(work / "source-info.json")
    page_count = int(source_info.get("slideCount") or 0)
    if page_count < 1:
        raise ValueError("source-info.json 缺少有效 slideCount")
    pages: list[dict[str, Any]] = []
    for page_no in range(1, page_count + 1):
        slide = read_json(work / "slides" / f"slide-{page_no:04d}.json")
        slide_size = slide.get("slideSizeEmu") or {}
        slide_cy = int(slide_size.get("cy") or 0)
        if slide_cy < 1:
            raise ValueError(f"第 {page_no} 页缺少 slideSizeEmu.cy")
        blocks: list[dict[str, Any]] = []
        for shape in slide.get("shapes") or []:
            if not isinstance(shape, dict):
                continue
            text = str(shape.get("text") or "")
            if not text.strip() or excluded_shape(shape, slide_cy):
                continue
            blocks.append(
                {
                    "shapeId": str(
                        shape.get("shapeId") or f"index-{shape.get('shapeIndex', 0)}"
                    ),
                    "shapeIndex": int(shape.get("shapeIndex") or 0),
                    "name": str(shape.get("name") or ""),
                    "role": role_for(shape, text),
                    "text": text,
                    "textSha256": sha256_text(text),
                    "length": len(text),
                    "boxEmu": shape.get("boxEmu"),
                }
            )
        pages.append(
            {
                "pageNo": page_no,
                "nativeTextSha256": sha256_text(str(slide.get("nativeText") or "")),
                "textBlocks": blocks,
                "questionTextCandidateShapeIds": [
                    block["shapeId"]
                    for block in blocks
                    if block["role"] == "question_text_candidate"
                ],
            }
        )
    source = source_info.get("source") or {}
    return {
        "protocol": "qulifang-ppt-recognition-evidence",
        "version": 1,
        "sourceFilename": source.get("filename") or source_info.get("sourceFilename"),
        "sourceSha256": source.get("sha256") or source_info.get("sourceSha256"),
        "pageCount": page_count,
        "pages": pages,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 PPT 逐文本框识别证据")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    work = args.work.expanduser().resolve()
    output = args.output.expanduser().resolve()
    try:
        result = build(work)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"错误：{exc}\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "pageCount": result["pageCount"],
                "textBlockCount": sum(len(page["textBlocks"]) for page in result["pages"]),
                "questionTextCandidateCount": sum(
                    len(page["questionTextCandidateShapeIds"]) for page in result["pages"]
                ),
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
