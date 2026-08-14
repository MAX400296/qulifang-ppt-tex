#!/usr/bin/env python3
"""校验题目配图的白底、留白、可追溯性与人工视觉复核状态。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw


ALLOWED_METHODS = {"shape-candidates", "powerpoint-selection", "raster-semantic"}
DEFAULT_MIN_SOURCE_DPI = 360
DEFAULT_MIN_SHORT_EDGE = 600
DEFAULT_MIN_LONG_EDGE = 800


def looks_like_semantic_figure(candidate: dict[str, Any]) -> bool:
    """识别需要逐一交代去向的图形候选，避免多图题只保留题干点名的一张。"""

    labels = [str(value).strip() for value in candidate.get("includedTextLabels") or []]
    if any(re.fullmatch(r"图\s*[（(]?[0-9一二三四五六七八九十]+[）)]?", label) for label in labels):
        return True
    point_labels = {label for label in labels if re.fullmatch(r"[A-Z]", label)}
    return len(point_labels) >= 3


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 顶层必须是对象")
    return value


def local_file(base: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value.strip() or "://" in value or value.startswith("data:"):
        raise ValueError("图片路径必须是分析目录内的本地文件")
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"图片必须位于分析目录内：{value}") from exc
    if not resolved.is_file():
        raise ValueError(f"图片不存在：{value}")
    return resolved


def foreground_mask(image: Image.Image) -> Image.Image:
    difference = ImageChops.difference(image.convert("RGB"), Image.new("RGB", image.size, "white"))
    return difference.convert("L").point(lambda value: 255 if value >= 10 else 0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_metrics(path: Path) -> dict[str, Any]:
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    width, height = image.size
    if width < 1 or height < 1:
        raise ValueError("图片尺寸无效")
    pixels = image.load()
    edge_band = max(2, round(min(width, height) * 0.012))
    edge_total = 0
    edge_nonwhite = 0
    for y in range(height):
        for x in range(width):
            if x < edge_band or x >= width - edge_band or y < edge_band or y >= height - edge_band:
                edge_total += 1
                red, green, blue = pixels[x, y]
                if min(red, green, blue) < 248:
                    edge_nonwhite += 1
    mask = foreground_mask(image)
    bbox = mask.getbbox()
    foreground_pixels = sum(mask.histogram()[1:])
    if bbox is None:
        padding_ratio = 0.0
    else:
        left, top, right, bottom = bbox
        padding_ratio = min(left / width, top / height, (width - right) / width, (height - bottom) / height)
    return {
        "width": width,
        "height": height,
        "edgeNonWhiteRatio": round(edge_nonwhite / max(1, edge_total), 6),
        "foregroundRatio": round(foreground_pixels / (width * height), 6),
        "minimumPaddingRatio": round(padding_ratio, 6),
        "foregroundBbox": list(bbox) if bbox else None,
    }


def create_contact_sheet(entries: list[tuple[str, Path]], output: Path) -> None:
    cell_width, image_height, label_height, columns = 600, 340, 34, 4
    rows = max(1, (len(entries) + columns - 1) // columns)
    sheet = Image.new("RGB", (cell_width * columns, (image_height + label_height) * rows), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (asset_id, path) in enumerate(entries):
        with Image.open(path) as opened:
            preview = opened.convert("RGB")
            preview.thumbnail((cell_width - 12, image_height - 12))
        x = (index % columns) * cell_width
        y = (index // columns) * (image_height + label_height)
        sheet.paste(preview, (x + 6, y + 6))
        draw.text((x + 8, y + image_height + 7), asset_id, fill="black")
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, format="JPEG", quality=90)


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 PPT 题目配图质量")
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--contact-sheet", type=Path, required=True)
    parser.add_argument("--allow-pending-review", action="store_true")
    parser.add_argument("--min-source-dpi", type=int, default=DEFAULT_MIN_SOURCE_DPI)
    parser.add_argument("--min-short-edge", type=int, default=DEFAULT_MIN_SHORT_EDGE)
    parser.add_argument("--min-long-edge", type=int, default=DEFAULT_MIN_LONG_EDGE)
    args = parser.parse_args()
    if args.min_source_dpi < 96:
        parser.error("--min-source-dpi 不能低于 96")
    if args.min_short_edge < 160 or args.min_long_edge < 240:
        parser.error("高清尺寸门槛不能低于短边 160 px、长边 240 px")
    if args.min_long_edge < args.min_short_edge:
        parser.error("--min-long-edge 不能小于 --min-short-edge")
    analysis_path = args.analysis.expanduser().resolve()
    base = analysis_path.parent
    analysis = read_json(analysis_path)
    candidates_value = analysis.get("figureCandidates")
    candidate_records: dict[str, dict[str, Any]] = {}
    if isinstance(candidates_value, str) and candidates_value.strip():
        try:
            candidate_file = local_file(base, candidates_value)
            candidate_data = read_json(candidate_file)
            if (
                candidate_data.get("protocol") != "qulifang-ppt-figure-candidates"
                or candidate_data.get("version") != 1
            ):
                parser.error("figureCandidates 协议必须是 qulifang-ppt-figure-candidates/v1")
            candidate_records = {
                str(candidate.get("id")): candidate
                for page in candidate_data.get("pages") or []
                if isinstance(page, dict)
                for candidate in page.get("candidates") or []
                if isinstance(candidate, dict) and candidate.get("id")
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(f"figureCandidates 无法读取：{exc}")
    elif analysis.get("assets"):
        parser.error("存在配图时必须设置 figureCandidates")
    assets = analysis.get("assets") or []
    if not isinstance(assets, list):
        parser.error("ppt-analysis.json 的 assets 必须是数组")
    errors: list[str] = []
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    contacts: list[tuple[str, Path]] = []
    asset_ids: list[str] = []
    selected_candidate_ids: set[str] = set()
    for position, asset in enumerate(assets):
        label = f"assets[{position}]"
        if not isinstance(asset, dict):
            errors.append(f"{label} 必须是对象")
            continue
        asset_id = str(asset.get("id") or "").strip()
        if not asset_id:
            errors.append(f"{label}.id 不能为空")
            continue
        asset_ids.append(asset_id)
        asset_errors: list[str] = []
        try:
            source = local_file(base, asset.get("source"))
            metrics = image_metrics(source)
            image_hash = sha256(source)
            contacts.append((asset_id, source))
        except (OSError, ValueError) as exc:
            asset_errors.append(str(exc))
            metrics = {}
            image_hash = ""
        quality = asset.get("figureQuality")
        if not isinstance(quality, dict):
            asset_errors.append("缺少 figureQuality")
            quality = {}
        method = quality.get("cropMethod")
        if method not in ALLOWED_METHODS:
            asset_errors.append(f"cropMethod 必须是 {', '.join(sorted(ALLOWED_METHODS))} 之一")
        if quality.get("background") != "white":
            asset_errors.append("background 必须是 white")
        padding = quality.get("paddingRatio")
        if not isinstance(padding, (int, float)) or isinstance(padding, bool) or not 0.02 <= float(padding) <= 0.08:
            asset_errors.append("paddingRatio 必须在 0.02–0.08 之间")
        if method == "shape-candidates":
            candidate_ids = [str(value) for value in quality.get("candidateIds") or []]
            selected_candidate_ids.update(candidate_ids)
            if not candidate_ids:
                asset_errors.append("shape-candidates 缺少 candidateIds")
            source_shape_ids = [str(value) for value in quality.get("sourceShapeIds") or []]
            if not source_shape_ids:
                asset_errors.append("shape-candidates 缺少 sourceShapeIds")
            selected_candidates = [candidate_records.get(value) for value in candidate_ids]
            if any(candidate is None for candidate in selected_candidates):
                asset_errors.append("candidateIds 在 figureCandidates 中不存在")
            elif selected_candidates:
                expected_shape_ids = {
                    str(shape_id)
                    for candidate in selected_candidates
                    if isinstance(candidate, dict)
                    for shape_id in candidate.get("shapeIds") or []
                }
                if set(source_shape_ids) != expected_shape_ids:
                    asset_errors.append("sourceShapeIds 与候选形状及自动补入标签不一致")
                expected_labels = {
                    str(text)
                    for candidate in selected_candidates
                    if isinstance(candidate, dict)
                    for text in candidate.get("includedTextLabels") or []
                    if str(text).strip()
                }
                recorded_labels = {
                    str(text) for text in quality.get("includedTextLabels") or [] if str(text).strip()
                }
                if recorded_labels != expected_labels:
                    asset_errors.append("includedTextLabels 与候选中的标签证据不一致")
            crop_metadata_value = quality.get("cropMetadata")
            if not isinstance(crop_metadata_value, str) or not crop_metadata_value.strip():
                asset_errors.append("shape-candidates 缺少 cropMetadata")
            else:
                try:
                    crop_metadata = read_json(local_file(base, crop_metadata_value))
                    if crop_metadata.get("assetId") != asset_id:
                        asset_errors.append("cropMetadata.assetId 不一致")
                    if set(str(value) for value in crop_metadata.get("sourceShapeIds") or []) != set(
                        source_shape_ids
                    ):
                        asset_errors.append("cropMetadata.sourceShapeIds 不一致")
                    if float(crop_metadata.get("renderCoverageRatio") or 0) < 0.995:
                        asset_errors.append("裁图像素覆盖率低于 99.5%，可能发生漏截")
                    if crop_metadata.get("qualityTier") != "high-definition":
                        asset_errors.append("cropMetadata.qualityTier 必须是 high-definition")
                    source_dpi = float(crop_metadata.get("sourcePageEffectiveDpi") or 0)
                    if source_dpi < args.min_source_dpi:
                        asset_errors.append(
                            f"源页面有效 DPI 低于 {args.min_source_dpi}，不能保证高清配图"
                        )
                    output_size = crop_metadata.get("outputSize")
                    if isinstance(output_size, dict) and metrics:
                        if (
                            int(output_size.get("width") or 0) != metrics["width"]
                            or int(output_size.get("height") or 0) != metrics["height"]
                        ):
                            asset_errors.append("cropMetadata.outputSize 与实际图片尺寸不一致")
                    else:
                        asset_errors.append("cropMetadata 缺少有效 outputSize")
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    asset_errors.append(f"cropMetadata 无法读取：{exc}")
        semantic_review = quality.get("semanticReview")
        if not (
            isinstance(semantic_review, dict)
            and semantic_review.get("labelsVerified") is True
            and semantic_review.get("lineEndpointsVerified") is True
            and semantic_review.get("unrelatedContentExcluded") is True
            and semantic_review.get("localCropOpened") is True
        ):
            asset_errors.append("semanticReview 必须确认标签、线端点、无关内容和本地裁图")
        visual_review = quality.get("visualReview")
        reviewed = (
            isinstance(visual_review, dict)
            and visual_review.get("status") == "passed"
            and visual_review.get("reviewedAgainstPage") is True
        )
        if not reviewed:
            message = "尚未完成原页叠框图与白底裁图的人工视觉复核"
            if args.allow_pending_review:
                warnings.append(f"{asset_id}: {message}")
            else:
                asset_errors.append(message)
        if metrics:
            short_edge = min(metrics["width"], metrics["height"])
            long_edge = max(metrics["width"], metrics["height"])
            if short_edge < args.min_short_edge or long_edge < args.min_long_edge:
                asset_errors.append(
                    "输出分辨率不足："
                    f"实际 {metrics['width']}×{metrics['height']}px，"
                    f"短边至少 {args.min_short_edge}px 且长边至少 {args.min_long_edge}px"
                )
            if metrics["edgeNonWhiteRatio"] > 0.002:
                asset_errors.append("图形触碰外边缘，可能漏截或留白不足")
            if metrics["minimumPaddingRatio"] < 0.015:
                asset_errors.append("有效图形四周留白小于 1.5%")
            if metrics["foregroundRatio"] < 0.001:
                asset_errors.append("有效图形像素过少")
        errors.extend(f"{asset_id}: {message}" for message in asset_errors)
        results.append({"assetId": asset_id, "sha256": image_hash, "ok": not asset_errors, "metrics": metrics, "errors": asset_errors})

    exclusions_raw = analysis.get("figureCandidateExclusions") or []
    exclusions: dict[str, dict[str, Any]] = {}
    if not isinstance(exclusions_raw, list):
        errors.append("figureCandidateExclusions 必须是数组")
        exclusions_raw = []
    for position, exclusion in enumerate(exclusions_raw):
        label = f"figureCandidateExclusions[{position}]"
        if not isinstance(exclusion, dict):
            errors.append(f"{label} 必须是对象")
            continue
        candidate_id = str(exclusion.get("candidateId") or "").strip()
        candidate = candidate_records.get(candidate_id)
        if not candidate:
            errors.append(f"{label}.candidateId 在 figureCandidates 中不存在")
            continue
        if candidate_id in exclusions:
            errors.append(f"候选图排除记录重复：{candidate_id}")
            continue
        page_match = re.match(r"p(\d{4})-c\d{3}$", candidate_id)
        candidate_page = int(page_match.group(1)) if page_match else None
        if exclusion.get("sourcePage") != candidate_page:
            errors.append(f"{label}.sourcePage 与候选页码不一致")
        reason = str(exclusion.get("reason") or "").strip()
        if len(reason) < 8:
            errors.append(f"{label}.reason 必须填写具体视觉排除理由")
        if re.search(r"只.{0,4}(提到|使用|利用).{0,4}图", reason):
            errors.append(f"{label}.reason 不能仅以题干点名另一张图为排除依据")
        review = exclusion.get("visualReview")
        if not (
            isinstance(review, dict)
            and review.get("status") == "passed"
            and review.get("reviewedAgainstPage") is True
        ):
            errors.append(f"{label}.visualReview 必须完成原页视觉复核")
        if candidate_id in selected_candidate_ids:
            errors.append(f"候选图 {candidate_id} 已被资产保留，不能同时排除")
        exclusions[candidate_id] = exclusion

    question_pages = {
        int(page.get("pageNo"))
        for page in analysis.get("pages") or []
        if isinstance(page, dict)
        and page.get("pageType") == "question"
        and isinstance(page.get("pageNo"), int)
    }
    required_candidate_ids = {
        candidate_id
        for candidate_id, candidate in candidate_records.items()
        if looks_like_semantic_figure(candidate)
        and (match := re.match(r"p(\d{4})-c\d{3}$", candidate_id))
        and int(match.group(1)) in question_pages
    }
    unaccounted_candidate_ids = sorted(
        required_candidate_ids - selected_candidate_ids - set(exclusions)
    )
    for candidate_id in unaccounted_candidate_ids:
        errors.append(
            f"疑似语义配图候选 {candidate_id} 未被题目资产保留，也没有视觉排除记录"
        )
    create_contact_sheet(contacts, args.contact_sheet.expanduser().resolve())
    report = {
        "protocol": "qulifang-ppt-figure-validation",
        "version": 1,
        "ok": not errors,
        "assetCount": len(assets),
        "assetIds": asset_ids,
        "assetHashes": {result["assetId"]: result["sha256"] for result in results},
        "candidateCoverage": {
            "requiredCandidateIds": sorted(required_candidate_ids),
            "selectedCandidateIds": sorted(selected_candidate_ids),
            "excludedCandidateIds": sorted(exclusions),
            "unaccountedCandidateIds": unaccounted_candidate_ids,
        },
        "errors": errors,
        "warnings": warnings,
        "qualityProfile": {
            "minimumSourceEffectiveDpi": args.min_source_dpi,
            "minimumShortEdge": args.min_short_edge,
            "minimumLongEdge": args.min_long_edge,
            "interpolationUpscalingAccepted": False,
        },
        "results": results,
        "contactSheet": str(args.contact_sheet.expanduser().resolve()),
    }
    report_path = args.report.expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
