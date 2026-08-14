#!/usr/bin/env python3
"""按 Shape 候选裁取图形、移除幻灯片底色并输出白底 PNG。"""

from __future__ import annotations

import argparse
import colorsys
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw


DEFAULT_MIN_SHORT_EDGE = 600
DEFAULT_MIN_LONG_EDGE = 800


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 顶层必须是对象")
    return value


def union_box(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def expand_box(
    box: tuple[int, int, int, int], margin_x: int, margin_y: int, width: int, height: int
) -> tuple[int, int, int, int]:
    return (
        max(0, box[0] - margin_x),
        max(0, box[1] - margin_y),
        min(width, box[2] + margin_x),
        min(height, box[3] + margin_y),
    )


def estimate_background(image: Image.Image, regions: list[tuple[int, int, int, int]]) -> tuple[int, int, int]:
    pixels = image.load()
    width, height = image.size
    samples: list[tuple[int, int, int]] = []
    corner = max(8, round(min(width, height) * 0.025))
    for x0, y0 in ((0, 0), (width - corner, 0), (0, height - corner), (width - corner, height - corner)):
        for y in range(y0, min(height, y0 + corner), max(1, corner // 8)):
            for x in range(x0, min(width, x0 + corner), max(1, corner // 8)):
                samples.append(pixels[x, y][:3])
    for box in regions:
        x0, y0, x1, y1 = box
        step = max(1, round(max(x1 - x0, y1 - y0) / 120))
        for x in range(x0, x1, step):
            samples.append(pixels[x, y0][:3])
            samples.append(pixels[x, max(y0, y1 - 1)][:3])
        for y in range(y0, y1, step):
            samples.append(pixels[x0, y][:3])
            samples.append(pixels[max(x0, x1 - 1), y][:3])
    if not samples:
        return 255, 255, 255
    quantized = Counter((r // 8 * 8, g // 8 * 8, b // 8 * 8) for r, g, b in samples)
    return quantized.most_common(1)[0][0]


def normalize_white(
    crop: Image.Image,
    allowed_regions: list[tuple[int, int, int, int]],
    background: tuple[int, int, int],
    threshold: int,
) -> Image.Image:
    source = crop.convert("RGB")
    allowed = Image.new("L", source.size, 0)
    draw = ImageDraw.Draw(allowed)
    for region in allowed_regions:
        draw.rectangle(region, fill=255)
    bg_luminance = sum(background) / 3.0
    result: list[tuple[int, int, int]] = []
    source_bytes = source.tobytes()
    allowed_bytes = allowed.tobytes()
    for index, permitted in enumerate(allowed_bytes):
        offset = index * 3
        red, green, blue = source_bytes[offset : offset + 3]
        if not permitted:
            result.append((255, 255, 255))
            continue
        distance = max(abs(red - background[0]), abs(green - background[1]), abs(blue - background[2]))
        if distance <= threshold:
            result.append((255, 255, 255))
            continue
        high = max(red, green, blue)
        low = min(red, green, blue)
        saturation = 0.0 if high == 0 else (high - low) / high
        lightness = colorsys.rgb_to_hls(red / 255.0, green / 255.0, blue / 255.0)[1]
        # 深色课件上的白色/浅灰线条在白底上改为深色；彩色语义线保持原色。
        if bg_luminance < 150 and lightness >= 0.66 and saturation <= 0.24:
            result.append((32, 32, 32))
        else:
            result.append((red, green, blue))
    output = Image.new("RGB", source.size, "white")
    output.putdata(result)
    return output


def foreground_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    difference = ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, "white"))
    mask = difference.convert("L").point(lambda value: 255 if value >= 10 else 0)
    return mask.getbbox()


def foreground_pixels(image: Image.Image) -> int:
    difference = ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, "white"))
    mask = difference.convert("L").point(lambda value: 255 if value >= 10 else 0)
    return sum(mask.histogram()[1:])


def main() -> int:
    parser = argparse.ArgumentParser(description="把 PPT Shape 候选渲染为白底语义配图")
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--candidate", action="append", required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    parser.add_argument("--review-output", type=Path, required=True)
    parser.add_argument("--padding", type=float, default=0.04)
    parser.add_argument("--background-threshold", type=int, default=24)
    parser.add_argument(
        "--min-short-edge",
        type=int,
        default=DEFAULT_MIN_SHORT_EDGE,
        help="高清配图最短边像素门槛；默认 600",
    )
    parser.add_argument(
        "--min-long-edge",
        type=int,
        default=DEFAULT_MIN_LONG_EDGE,
        help="高清配图最长边像素门槛；默认 800",
    )
    args = parser.parse_args()
    if not 0.02 <= args.padding <= 0.08:
        parser.error("--padding 必须在 0.02–0.08 之间")
    if not 8 <= args.background_threshold <= 64:
        parser.error("--background-threshold 必须在 8–64 之间")
    if args.min_short_edge < 160 or args.min_long_edge < 240:
        parser.error("高清尺寸门槛不能低于短边 160 px、长边 240 px")
    if args.min_long_edge < args.min_short_edge:
        parser.error("--min-long-edge 不能小于 --min-short-edge")
    data = read_json(args.candidates.expanduser().resolve())
    page = next((item for item in data.get("pages") or [] if item.get("pageNo") == args.page), None)
    if not isinstance(page, dict):
        parser.error(f"候选文件中不存在第 {args.page} 页")
    requested = set(args.candidate)
    selected = [item for item in page.get("candidates") or [] if item.get("id") in requested]
    found = {str(item.get("id")) for item in selected}
    missing = sorted(requested - found)
    if missing:
        parser.error(f"候选 ID 不存在：{', '.join(missing)}")
    page_image = Path(str(page.get("pageImage") or "")).expanduser().resolve()
    if not page_image.is_file():
        parser.error(f"页面图片不存在：{page_image}")
    source = Image.open(page_image).convert("RGB")
    width, height = source.size
    margin_x = max(4, round(width * 0.012))
    margin_y = max(4, round(height * 0.012))
    selected_boxes = [tuple(int(value) for value in item["bboxPx"]) for item in selected]
    expanded_boxes = [expand_box(box, margin_x, margin_y, width, height) for box in selected_boxes]
    coarse = union_box(expanded_boxes)
    background = estimate_background(source, expanded_boxes)
    coarse_image = source.crop(coarse)
    allowed_regions = [
        (box[0] - coarse[0], box[1] - coarse[1], box[2] - coarse[0], box[3] - coarse[1])
        for box in expanded_boxes
    ]
    normalized = normalize_white(coarse_image, allowed_regions, background, args.background_threshold)
    content_box = foreground_box(normalized)
    if content_box is None:
        parser.error("选中的候选未产生可见图形，请重新选择候选")
    content = normalized.crop(content_box)
    padding_px = max(8, round(max(content.size) * args.padding))
    final = Image.new("RGB", (content.width + padding_px * 2, content.height + padding_px * 2), "white")
    final.paste(content, (padding_px, padding_px))
    short_edge = min(final.size)
    long_edge = max(final.size)
    if short_edge < args.min_short_edge or long_edge < args.min_long_edge:
        parser.error(
            "配图清晰度不足："
            f"输出 {final.width}×{final.height}px，要求短边至少 {args.min_short_edge}px、"
            f"长边至少 {args.min_long_edge}px；请用 PowerPoint 原生 PDF 以 480 DPI 或更高重新渲染页面，"
            "不要通过插值放大伪造清晰度"
        )
    normalized_foreground_pixels = foreground_pixels(normalized)
    output_foreground_pixels = foreground_pixels(final)
    render_coverage_ratio = (
        output_foreground_pixels / normalized_foreground_pixels
        if normalized_foreground_pixels
        else 0.0
    )
    output = args.output.expanduser().resolve()
    metadata_output = args.metadata_output.expanduser().resolve()
    review_output = args.review_output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    review_output.parent.mkdir(parents=True, exist_ok=True)
    final.save(output, format="PNG", optimize=True)

    review = source.copy()
    review_draw = ImageDraw.Draw(review)
    for box in selected_boxes:
        review_draw.rectangle(box, outline="#00ff88", width=max(4, width // 700))
    review_draw.rectangle(coarse, outline="#ffcc00", width=max(4, width // 700))
    review.save(review_output, format="PNG", optimize=True)

    shape_ids = list(
        dict.fromkeys(
            str(shape_id)
            for candidate in selected
            for shape_id in candidate.get("shapeIds") or []
        )
    )
    included_text_labels = list(
        dict.fromkeys(
            str(label)
            for candidate in selected
            for label in candidate.get("includedTextLabels") or []
            if str(label).strip()
        )
    )
    auto_label_shape_ids = list(
        dict.fromkeys(
            str(shape_id)
            for candidate in selected
            for shape_id in candidate.get("autoIncludedLabelShapeIds") or []
        )
    )
    metadata = {
        "assetId": args.asset_id,
        "sourcePage": args.page,
        "candidateIds": [str(item["id"]) for item in selected],
        "sourceShapeIds": shape_ids,
        "autoIncludedLabelShapeIds": auto_label_shape_ids,
        "includedTextLabels": included_text_labels,
        "sourceBbox": list(union_box(selected_boxes)),
        "coarseBbox": list(coarse),
        "cropMethod": "shape-candidates",
        "background": "white",
        "detectedBackgroundRgb": list(background),
        "paddingRatio": args.padding,
        "qualityTier": "high-definition",
        "sourcePagePixelSize": {"width": width, "height": height},
        "sourcePageEffectiveDpi": page.get("effectiveDpi"),
        "output": str(output),
        "outputSize": {"width": final.width, "height": final.height},
        "minimumOutputSize": {
            "shortEdge": args.min_short_edge,
            "longEdge": args.min_long_edge,
        },
        "sourceForegroundPixels": normalized_foreground_pixels,
        "outputForegroundPixels": output_foreground_pixels,
        "renderCoverageRatio": round(render_coverage_ratio, 6),
        "visualReview": {"status": "pending", "reviewedAgainstPage": False},
    }
    metadata_output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, **metadata}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
