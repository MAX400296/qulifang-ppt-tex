#!/usr/bin/env python3
"""把 PPT 分析、原 PPTX 与 TEX 题包封装为管理端可直接导入的课件代码包。"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile


PROTOCOL = "qulifang-ppt-tex"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取 {path.name}：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} 不是有效 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 顶层必须是对象")
    return value


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def signature_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def block_signature(value: Any, asset_hashes: dict[str, str]) -> tuple[Any, ...]:
    if isinstance(value, str):
        return (("text", signature_text(value)),)
    if not isinstance(value, list):
        return (("raw", signature_text(value)),)
    result: list[tuple[str, str]] = []
    for block in value:
        if isinstance(block, str):
            result.append(("text", signature_text(block)))
        elif not isinstance(block, dict):
            result.append(("raw", signature_text(block)))
        elif block.get("kind") == "image":
            asset_id = str(block.get("assetId") or "")
            result.append(("image", asset_hashes.get(asset_id, asset_id or "<image>")))
        else:
            result.append((str(block.get("kind") or ""), signature_text(block.get("value", ""))))
    return tuple(result)


def question_signature(
    question: dict[str, Any], asset_hashes: dict[str, str]
) -> tuple[Any, ...] | None:
    stem = block_signature(question.get("stem"), asset_hashes)
    if not any(value for _, value in stem):
        return None
    options = question.get("options") or {}
    if not isinstance(options, dict):
        options = {}
    option_signature = tuple(
        (str(key).strip().upper(), block_signature(options[key], asset_hashes))
        for key in sorted(options, key=lambda item: str(item).upper())
    )
    return stem, option_signature


def build_question_map(ir: dict[str, Any], ir_path: Path) -> list[dict[str, Any]]:
    raw_assets = ir.get("assets") or []
    if not isinstance(raw_assets, list):
        raise ValueError("questions.ir.json 的 assets 必须是数组")
    asset_hashes: dict[str, str] = {}
    for asset in raw_assets:
        if not isinstance(asset, dict):
            raise ValueError("questions.ir.json 的图片记录必须是对象")
        asset_id = str(asset.get("id") or "").strip()
        source_value = asset.get("source")
        if not asset_id or not isinstance(source_value, str) or not source_value:
            raise ValueError("questions.ir.json 的图片缺少 id 或 source")
        source = Path(source_value).expanduser()
        if not source.is_absolute():
            source = (ir_path.parent / source).resolve()
        if not source.is_file():
            raise ValueError(f"题目图片不存在：{source}")
        asset_hashes[asset_id] = file_sha256(source)

    raw_questions = ir.get("questions")
    if not isinstance(raw_questions, list):
        raise ValueError("questions.ir.json 的 questions 必须是数组")
    seen: dict[tuple[Any, ...], str] = {}
    mapping: list[dict[str, Any]] = []
    for position, question in enumerate(raw_questions, start=1):
        if not isinstance(question, dict):
            raise ValueError(f"questions.ir.json 第 {position} 题必须是对象")
        question_ref = str(question.get("sourceRef") or "").strip()
        page_no = question.get("sourcePage")
        item_index = question.get("sourceItemIndex")
        if (
            not re.fullmatch(r"p\d{4}-i\d{2}", question_ref)
            or not isinstance(page_no, int)
            or not isinstance(item_index, int)
        ):
            raise ValueError(f"questions.ir.json 第 {position} 题缺少有效的页面映射")
        fingerprint = question_signature(question, asset_hashes)
        if fingerprint is None:
            raise ValueError(f"questions.ir.json 第 {position} 题题干为空")
        question_number = seen.get(fingerprint)
        if question_number is None:
            question_number = str(len(seen) + 1)
            seen[fingerprint] = question_number
        mapping.append(
            {
                "pageNo": page_no,
                "itemIndex": item_index,
                "questionRef": question_ref,
                "questionNumber": question_number,
            }
        )
    return mapping


def validate_question_package(path: Path, *, expected_count: int) -> None:
    if not path.is_file():
        raise ValueError(f"题目包不存在：{path}")
    try:
        with ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            report = json.loads(archive.read("conversion-report.json").decode("utf-8"))
    except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("题目包不是有效的 educationapp-question-tex ZIP") from exc
    if manifest.get("protocol") != "educationapp-question-tex" or manifest.get("version") != 1:
        raise ValueError("题目包协议必须是 educationapp-question-tex/v1")
    if report.get("ok") is not True:
        raise ValueError("题目包 conversion-report.json 尚未通过校验")
    if report.get("questionCount") != expected_count:
        raise ValueError(
            f"题目包去重后题数应为 {expected_count}，实际为 {report.get('questionCount')}"
        )


def validate_existing_output(path: Path) -> None:
    try:
        with ZipFile(path) as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"拒绝覆盖非 {PROTOCOL} 输出：{path}") from exc
    if manifest.get("protocol") != PROTOCOL:
        raise ValueError(f"拒绝覆盖非 {PROTOCOL} 输出：{path}")


def resolve_analysis_file(analysis_path: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "://" in value or value.startswith("data:"):
        raise ValueError(f"{label} 必须是分析目录内的本地文件")
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (analysis_path.parent / candidate).resolve()
    try:
        resolved.relative_to(analysis_path.parent)
    except ValueError as exc:
        raise ValueError(f"{label} 必须位于分析目录内") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} 文件不存在：{resolved}")
    return resolved


def validate_figure_report(analysis: dict[str, Any], analysis_path: Path) -> Path | None:
    assets = analysis.get("assets") or []
    if not isinstance(assets, list):
        raise ValueError("ppt-analysis.json 的 assets 必须是数组")
    if not assets:
        return None
    report_path = resolve_analysis_file(
        analysis_path, analysis.get("figureValidationReport"), "figureValidationReport"
    )
    report = load_json(report_path)
    if (
        report.get("protocol") != "qulifang-ppt-figure-validation"
        or report.get("version") != 1
        or report.get("ok") is not True
    ):
        raise ValueError("figure-validation-report.json 尚未通过 qulifang-ppt-figure-validation/v1 校验")
    expected_ids: set[str] = set()
    expected_hashes: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("ppt-analysis.json 的图片记录必须是对象")
        asset_id = str(asset.get("id") or "").strip()
        if not asset_id:
            raise ValueError("ppt-analysis.json 的图片缺少 id")
        source = resolve_analysis_file(analysis_path, asset.get("source"), f"图片 {asset_id}")
        expected_ids.add(asset_id)
        expected_hashes[asset_id] = file_sha256(source)
    if set(str(value) for value in report.get("assetIds") or []) != expected_ids:
        raise ValueError("figure-validation-report.json 覆盖的图片 ID 与分析文件不一致")
    if report.get("assetHashes") != expected_hashes:
        raise ValueError("配图在白底质量校验后发生变化，请重新运行 validate_figure_assets.py")
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 EducationApp 可导入的 PPT 课件代码包")
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--ir", required=True, type=Path)
    parser.add_argument("--validation-report", required=True, type=Path)
    parser.add_argument("--source-pptx", required=True, type=Path)
    parser.add_argument("--question-package", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        help="输出 ZIP；未指定时保存到原 PPTX 所在目录",
    )
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()

    analysis_path = args.analysis.expanduser().resolve()
    ir_path = args.ir.expanduser().resolve()
    report_path = args.validation_report.expanduser().resolve()
    source_path = args.source_pptx.expanduser().resolve()
    output_path = (
        args.output.expanduser().resolve()
        if args.output is not None
        else source_path.with_name(f"{source_path.stem}-ppt-code-import.zip")
    )
    question_package_path = (
        args.question_package.expanduser().resolve()
        if args.question_package is not None
        else None
    )
    for required in (analysis_path, ir_path, report_path, source_path):
        if not required.is_file():
            parser.error(f"文件不存在：{required}")
    if source_path.suffix.lower() != ".pptx":
        parser.error("--source-pptx 必须是 .pptx 文件")

    analysis = load_json(analysis_path)
    ir = load_json(ir_path)
    report = load_json(report_path)
    if analysis.get("protocol") != PROTOCOL or analysis.get("version") != 1:
        parser.error("ppt-analysis.json 协议必须是 qulifang-ppt-tex/v1")
    try:
        figure_report_path = validate_figure_report(analysis, analysis_path)
    except ValueError as exc:
        parser.error(str(exc))
    if report.get("ok") is not True:
        parser.error("ppt-validation-report.json 尚未通过校验")
    question_map = build_question_map(ir, ir_path)
    unique_question_count = len({item["questionNumber"] for item in question_map})
    if question_map:
        if question_package_path is None:
            parser.error("存在题目页时必须提供 --question-package")
        try:
            validate_question_package(question_package_path, expected_count=unique_question_count)
        except ValueError as exc:
            parser.error(str(exc))
    elif question_package_path is not None:
        parser.error("没有题目页时不要提供 --question-package")

    analysis_source = analysis.get("source")
    analysis_page_count = (
        analysis_source.get("pageCount") if isinstance(analysis_source, dict) else None
    )
    if report.get("pageCount") != analysis_page_count:
        parser.error("分析文件与校验报告的页数不一致")
    source_digest = file_sha256(source_path)
    declared_digest = (
        str(analysis_source.get("sha256") or "").strip().lower()
        if isinstance(analysis_source, dict)
        else ""
    )
    if declared_digest and declared_digest != source_digest:
        parser.error("ppt-analysis.json 的 source.sha256 与原 PPTX 不一致")

    if output_path.exists():
        if not args.replace_existing:
            parser.error(f"输出已存在：{output_path}")
        try:
            validate_existing_output(output_path)
        except ValueError as exc:
            parser.error(str(exc))
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = {
        "protocol": PROTOCOL,
        "version": 1,
        "title": str(analysis.get("title") or ir.get("title") or source_path.stem).strip(),
        "source": {
            "filename": str(
                analysis_source.get("filename") if isinstance(analysis_source, dict) else ""
            ).strip()
            or source_path.name,
            "path": "source/source.pptx",
            "sha256": source_digest,
            "pageCount": analysis_page_count,
        },
        "analysis": "ppt-analysis.json",
        "validationReport": "ppt-validation-report.json",
        "figureValidationReport": "figure-validation-report.json" if figure_report_path else None,
        "questionPackage": "question-package.zip" if question_package_path else None,
        "questionMap": question_map,
        "lecturePageCount": report.get("lecturePageCount", 0),
        "questionPageCount": report.get("questionPageCount", 0),
        "questionCount": len(question_map),
        "uniqueQuestionCount": unique_question_count,
        "warningCount": len(report.get("warnings") or []),
        "needsReviewCount": report.get("needsReviewCount", 0),
    }
    # 最终上传包不携带本机绝对路径；高清页图与裁图仍保留在旁边的审核目录。
    packaged_analysis = json.loads(json.dumps(analysis, ensure_ascii=False))
    for page in packaged_analysis.get("pages") or []:
        if isinstance(page, dict) and isinstance(page.get("pageNo"), int):
            page["pageImage"] = f"pages/page-{page['pageNo']:04d}.png"
    for asset in packaged_analysis.get("assets") or []:
        if isinstance(asset, dict):
            asset_id = str(asset.get("id") or "asset")
            suffix = Path(str(asset.get("source") or "")).suffix.lower()
            asset["source"] = f"review-assets/{asset_id}{suffix}"
    analysis_bytes = (
        json.dumps(packaged_analysis, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with ZipFile(output_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("ppt-analysis.json", analysis_bytes)
        archive.write(report_path, "ppt-validation-report.json")
        if figure_report_path is not None:
            archive.write(figure_report_path, "figure-validation-report.json")
        archive.write(source_path, "source/source.pptx")
        if question_package_path is not None:
            archive.write(question_package_path, "question-package.zip")

    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output_path),
                "pageCount": analysis_page_count,
                "questionCount": len(question_map),
                "uniqueQuestionCount": unique_question_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
