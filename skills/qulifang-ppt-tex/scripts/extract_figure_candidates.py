#!/usr/bin/env python3
"""根据 PPT Shape 边界生成可追溯的语义配图候选与标注页。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


EXCLUDED_NAMES = {
    "footer",
    "header",
    "slide-title",
    "slide-subtitle",
    "page-number",
    "logo",
    "background",
    "question-label",
}
LABEL_TEXT_RE = re.compile(r"^[A-Za-z0-9\u3400-\u9fffπ°′″+\-×÷=:.()（）△∠⊥/\\\s]{1,16}$")
EMU_PER_INCH = 914_400


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 顶层必须是对象")
    return value


def page_path(work: Path, page_no: int) -> Path:
    candidates = [
        work / "pages" / f"page-{page_no:04d}.png",
        work / "pages" / f"page-{page_no:04d}.jpg",
        work / "pages" / f"page-{page_no:04d}.jpeg",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ValueError(f"第 {page_no} 页渲染图不存在")


def intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int], gap: int) -> bool:
    return not (
        a[2] + gap < b[0]
        or b[2] + gap < a[0]
        or a[3] + gap < b[1]
        or b[3] + gap < a[1]
    )


def box_distance(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    dx = max(a[0] - b[2], b[0] - a[2], 0)
    dy = max(a[1] - b[3], b[1] - a[3], 0)
    return (dx * dx + dy * dy) ** 0.5


def label_like(node: dict[str, Any], width: int, height: int) -> bool:
    text = " ".join(str(node.get("text") or "").split())
    if not text or not LABEL_TEXT_RE.fullmatch(text):
        return False
    box = node["bboxPx"]
    return (box[2] - box[0]) <= width * 0.16 and (box[3] - box[1]) <= height * 0.10


def union_box(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def shape_box_px(
    shape: dict[str, Any], slide_cx: int, slide_cy: int, width: int, height: int
) -> tuple[int, int, int, int] | None:
    box = shape.get("boxEmu")
    if not isinstance(box, dict):
        return None
    try:
        x = int(box["x"])
        y = int(box["y"])
        cx = int(box["cx"])
        cy = int(box["cy"])
    except (KeyError, TypeError, ValueError):
        return None
    x0 = round(x / slide_cx * width)
    y0 = round(y / slide_cy * height)
    x1 = round((x + cx) / slide_cx * width)
    y1 = round((y + cy) / slide_cy * height)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    x0 = max(0, min(width - 1, x0))
    y0 = max(0, min(height - 1, y0))
    x1 = max(x0 + 2, min(width, x1))
    y1 = max(y0 + 2, min(height, y1))
    return x0, y0, x1, y1


def excluded(shape: dict[str, Any], box: tuple[int, int, int, int], width: int, height: int) -> bool:
    name = str(shape.get("name") or "").lower()
    text = " ".join(str(shape.get("text") or "").split())
    box_width = box[2] - box[0]
    box_height = box[3] - box[1]
    area_ratio = box_width * box_height / (width * height)
    if any(token in name for token in EXCLUDED_NAMES):
        return True
    if area_ratio >= 0.80:
        return True
    if len(text) > 48:
        return True
    if box_width >= width * 0.72 and box_height <= height * 0.02:
        return True
    if box[1] >= height * 0.89:
        return True
    return False


def group_shapes(
    nodes: list[dict[str, Any]], width: int, height: int, gap_ratio: float
) -> list[list[dict[str, Any]]]:
    parent = list(range(len(nodes)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(left: int, right: int) -> None:
        root_left = find(left)
        root_right = find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    gap = max(6, round(min(width, height) * gap_ratio))
    for left in range(len(nodes)):
        for right in range(left + 1, len(nodes)):
            if intersects(nodes[left]["bboxPx"], nodes[right]["bboxPx"], gap):
                join(left, right)
    groups: dict[int, list[dict[str, Any]]] = {}
    for index, node in enumerate(nodes):
        groups.setdefault(find(index), []).append(node)
    return list(groups.values())


def build_page_candidates(
    slide: dict[str, Any], image_path: Path, gap_ratio: float, min_area_ratio: float
) -> tuple[dict[str, Any], Image.Image]:
    page_no = int(slide["pageNo"])
    slide_size = slide.get("slideSizeEmu")
    if not isinstance(slide_size, dict):
        raise ValueError(f"第 {page_no} 页缺少 slideSizeEmu；请重新运行 prepare_pptx.py")
    slide_cx = int(slide_size["cx"])
    slide_cy = int(slide_size["cy"])
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    effective_dpi = min(
        width / (slide_cx / EMU_PER_INCH),
        height / (slide_cy / EMU_PER_INCH),
    )
    nodes: list[dict[str, Any]] = []
    for shape in slide.get("shapes") or []:
        if not isinstance(shape, dict):
            continue
        bbox = shape_box_px(shape, slide_cx, slide_cy, width, height)
        if bbox is None or excluded(shape, bbox, width, height):
            continue
        nodes.append(
            {
                "shapeId": str(shape.get("shapeId") or f"index-{shape.get('shapeIndex', 0)}"),
                "shapeIndex": int(shape.get("shapeIndex", 0)),
                "kind": str(shape.get("kind") or ""),
                "name": str(shape.get("name") or ""),
                "text": " ".join(str(shape.get("text") or "").split()),
                "bboxPx": bbox,
            }
        )

    raw_groups = group_shapes(nodes, width, height, gap_ratio)
    # 已与另一幅图形连成组的文字标签属于那幅图，不能再被邻图重复吸附。
    anchored_label_ids = {
        node["shapeId"]
        for raw_group in raw_groups
        if any(not member["text"] or member["kind"] in {"pic", "graphicFrame", "grpSp"} for member in raw_group)
        for node in raw_group
        if node["text"]
    }
    candidates: list[dict[str, Any]] = []
    for group in raw_groups:
        bbox = union_box([node["bboxPx"] for node in group])
        box_width = bbox[2] - bbox[0]
        box_height = bbox[3] - bbox[1]
        area_ratio = box_width * box_height / (width * height)
        non_text_count = sum(1 for node in group if not node["text"])
        has_media = any(node["kind"] in {"pic", "graphicFrame", "grpSp"} for node in group)
        if area_ratio < min_area_ratio or box_width < width * 0.025 or box_height < height * 0.025:
            continue
        if non_text_count == 0 and not has_media:
            continue
        group_shape_ids = {node["shapeId"] for node in group}
        nearby_limit = max(10, round(min(width, height) * max(0.028, gap_ratio * 1.75)))
        nearby_labels = [
            node
            for node in nodes
            if node["shapeId"] not in group_shape_ids
            and node["shapeId"] not in anchored_label_ids
            and label_like(node, width, height)
            and box_distance(bbox, node["bboxPx"]) <= nearby_limit
        ]
        expanded_bbox = union_box([bbox, *[node["bboxPx"] for node in nearby_labels]])
        candidates.append(
            {
                "bboxPx": list(expanded_bbox),
                "coreBboxPx": list(bbox),
                "areaRatio": round(area_ratio, 6),
                "shapeIds": [node["shapeId"] for node in [*group, *nearby_labels]],
                "shapeIndexes": [node["shapeIndex"] for node in [*group, *nearby_labels]],
                "autoIncludedLabelShapeIds": [node["shapeId"] for node in nearby_labels],
                "includedTextLabels": list(
                    dict.fromkeys(node["text"] for node in [*group, *nearby_labels] if node["text"])
                ),
                "shapeRecords": [
                    {
                        "shapeId": node["shapeId"],
                        "kind": node["kind"],
                        "name": node["name"],
                        "text": node["text"],
                        "bboxPx": list(node["bboxPx"]),
                        "autoIncludedLabel": node in nearby_labels,
                    }
                    for node in [*group, *nearby_labels]
                ],
                "kindCounts": {
                    kind: sum(1 for node in group if node["kind"] == kind)
                    for kind in sorted({node["kind"] for node in group})
                },
                "textSnippets": [node["text"] for node in group if node["text"]][:12],
            }
        )
    candidates.sort(key=lambda item: (item["bboxPx"][1], item["bboxPx"][0]))
    for index, candidate in enumerate(candidates, 1):
        candidate["id"] = f"p{page_no:04d}-c{index:03d}"

    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    colors = ["#ff3b30", "#007aff", "#34c759", "#ff9500", "#af52de", "#00a7a7"]
    for index, candidate in enumerate(candidates):
        color = colors[index % len(colors)]
        bbox = tuple(candidate["bboxPx"])
        draw.rectangle(bbox, outline=color, width=max(3, width // 800))
        draw.rectangle((bbox[0], bbox[1], bbox[0] + 150, bbox[1] + 34), fill="white")
        draw.text((bbox[0] + 5, bbox[1] + 5), candidate["id"], fill=color)

    return (
        {
            "pageNo": page_no,
            "pageImage": str(image_path),
            "pixelSize": {"width": width, "height": height},
            "effectiveDpi": round(effective_dpi, 2),
            "candidateCount": len(candidates),
            "candidates": candidates,
        },
        annotated,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 PPT 配图 Shape 候选和标注页")
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--annotated-dir", type=Path, required=True)
    parser.add_argument("--gap-ratio", type=float, default=0.018)
    parser.add_argument("--min-area-ratio", type=float, default=0.0008)
    args = parser.parse_args()
    work = args.work.expanduser().resolve()
    output = args.output.expanduser().resolve()
    annotated_dir = args.annotated_dir.expanduser().resolve()
    if not 0.002 <= args.gap_ratio <= 0.08:
        parser.error("--gap-ratio 必须在 0.002–0.08 之间")
    if not 0.0001 <= args.min_area_ratio <= 0.05:
        parser.error("--min-area-ratio 必须在 0.0001–0.05 之间")
    info = read_json(work / "source-info.json")
    page_count = int(info.get("slideCount") or 0)
    if page_count < 1:
        parser.error("source-info.json 缺少有效 slideCount")
    pages: list[dict[str, Any]] = []
    annotated_dir.mkdir(parents=True, exist_ok=True)
    for page_no in range(1, page_count + 1):
        slide = read_json(work / "slides" / f"slide-{page_no:04d}.json")
        record, annotated = build_page_candidates(
            slide, page_path(work, page_no), args.gap_ratio, args.min_area_ratio
        )
        annotated.save(annotated_dir / f"page-{page_no:04d}-candidates.png")
        pages.append(record)
    result = {
        "protocol": "qulifang-ppt-figure-candidates",
        "version": 1,
        "pageCount": page_count,
        "gapRatio": args.gap_ratio,
        "pages": pages,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "pageCount": page_count, "candidateCount": sum(page["candidateCount"] for page in pages), "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
