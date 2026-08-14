#!/usr/bin/env python3
"""校验 PPT 逐页分析结果，并生成 qulifang-to-tex 标准题目 IR。"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any


PROTOCOL = "qulifang-ppt-tex"
QUESTION_REF_RE = re.compile(r"^p\d{4}-i\d{2}$")
ASSET_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
SUBQUESTION_MARKER_RE = re.compile(
    r"(?:[（(]\s*(?:\d+|[一二三四五六七八九十]+)\s*[)）]|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])"
)
ALLOWED_TARGETS = {
    "stem",
    "option:A",
    "option:B",
    "option:C",
    "option:D",
    "answer",
    "analysis",
}
QTYPE_MAP = {
    "choice": "选择题",
    "单选题": "选择题",
    "单项选择题": "选择题",
    "多选题": "选择题",
    "多项选择题": "选择题",
    "选择题": "选择题",
    "fill": "填空题",
    "填空题": "填空题",
    "判断题": "填空题",
    "app": "应用题",
    "应用题": "应用题",
    "解答题": "应用题",
    "计算题": "应用题",
    "证明题": "应用题",
}
ALLOWED_CROP_METHODS = {"shape-candidates", "powerpoint-selection", "raster-semantic"}
SOURCE_FIDELITY_THRESHOLD = 0.98
NATIVE_TRACE_THRESHOLD = 0.98
MIN_FIGURE_SOURCE_DPI = 360
MIN_FIGURE_SHORT_EDGE = 600
MIN_FIGURE_LONG_EDGE = 800
SCRIPT_DIGIT_TRANSLATION = str.maketrans(
    {
        "⁰": "^0",
        "¹": "^1",
        "²": "^2",
        "³": "^3",
        "⁴": "^4",
        "⁵": "^5",
        "⁶": "^6",
        "⁷": "^7",
        "⁸": "^8",
        "⁹": "^9",
        "₀": "_0",
        "₁": "_1",
        "₂": "_2",
        "₃": "_3",
        "₄": "_4",
        "₅": "_5",
        "₆": "_6",
        "₇": "_7",
        "₈": "_8",
        "₉": "_9",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"无法读取分析文件：{exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"分析文件不是有效 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("分析文件顶层必须是 JSON 对象")
    return value


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _image_type(path: Path) -> str | None:
    data = path.read_bytes()[:16]
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_ranges(question: dict[str, Any]) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    for evidence in question.get("sourceEvidence") or []:
        if not isinstance(evidence, dict):
            continue
        shape_id = str(evidence.get("shapeId") or "")
        start = evidence.get("start")
        end = evidence.get("end")
        if shape_id and isinstance(start, int) and isinstance(end, int) and start < end:
            ranges.append((shape_id, start, end))
    return ranges


def _validate_question_grouping(
    page_no: int,
    page: dict[str, Any],
    questions: list[Any],
    errors: list[str],
) -> bool:
    """默认一页一道题；阻止把共享题干的（1）（2）小问拆成多题。"""
    if len(questions) <= 1:
        return False

    grouping = page.get("questionGrouping")
    if not isinstance(grouping, dict) or grouping.get("mode") != "multiple":
        errors.append(
            f"第 {page_no} 页默认只能生成一道题；（1）（2）等小问必须合并到同一题干。"
            "仅当页面存在多个独立顶层题号时，才可设置 questionGrouping.mode=multiple"
        )
    else:
        reason = grouping.get("reason")
        labels = grouping.get("independentTopLevelLabels")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"第 {page_no} 页多题拆分必须填写 questionGrouping.reason")
        if (
            not isinstance(labels, list)
            or len(labels) < len(questions)
            or any(not isinstance(label, str) or not label.strip() for label in labels)
        ):
            errors.append(
                f"第 {page_no} 页多题拆分必须为每道题记录独立顶层题号 independentTopLevelLabels"
            )
        elif len({label.strip() for label in labels}) < len(questions):
            errors.append(f"第 {page_no} 页 independentTopLevelLabels 必须彼此不同")

    question_dicts = [question for question in questions if isinstance(question, dict)]
    source_texts = [str(question.get("sourceText") or "") for question in question_dicts]
    for position, source_text in enumerate(source_texts[1:], 2):
        if SUBQUESTION_MARKER_RE.match(source_text.lstrip()):
            errors.append(
                f"第 {page_no} 页第 {position} 个条目以小问标记开头，必须并入该页第一道题"
            )

    markers = [SUBQUESTION_MARKER_RE.search(text) for text in source_texts]
    if len(markers) >= 2 and all(marker is not None for marker in markers):
        prefixes = [
            _fidelity_units(text[: marker.start()])
            for text, marker in zip(source_texts, markers)
            if marker is not None
        ]
        common_prefix = prefixes[0] if prefixes else []
        for prefix in prefixes[1:]:
            limit = min(len(common_prefix), len(prefix))
            common_prefix = common_prefix[: next(
                (index for index in range(limit) if common_prefix[index] != prefix[index]),
                limit,
            )]
        if len(common_prefix) >= 6:
            errors.append(
                f"第 {page_no} 页多个条目共享同一题干并分别包含小问标记，必须合并为一道题"
            )

    evidence_sets = [_evidence_ranges(question) for question in question_dicts]
    for left in range(len(evidence_sets)):
        for right in range(left + 1, len(evidence_sets)):
            if any(
                left_shape == right_shape and max(left_start, right_start) < min(left_end, right_end)
                for left_shape, left_start, left_end in evidence_sets[left]
                for right_shape, right_start, right_end in evidence_sets[right]
            ):
                errors.append(
                    f"第 {page_no} 页第 {left + 1}、{right + 1} 个条目复用了同一源文区间，"
                    "说明它们共享题干，必须合并为一道题"
                )
                return True
    return True


def _validate_figure_quality(asset: dict[str, Any], label: str, errors: list[str]) -> None:
    quality = asset.get("figureQuality")
    if not isinstance(quality, dict):
        errors.append(f"{label}.figureQuality 不能为空")
        return
    method = quality.get("cropMethod")
    if method not in ALLOWED_CROP_METHODS:
        errors.append(f"{label}.figureQuality.cropMethod 非法：{method}")
    if quality.get("background") != "white":
        errors.append(f"{label}.figureQuality.background 必须是 white")
    padding = quality.get("paddingRatio")
    if (
        not isinstance(padding, (int, float))
        or isinstance(padding, bool)
        or not 0.02 <= float(padding) <= 0.08
    ):
        errors.append(f"{label}.figureQuality.paddingRatio 必须在 0.02–0.08 之间")
    if method == "shape-candidates":
        if not quality.get("candidateIds"):
            errors.append(f"{label}.figureQuality 缺少 candidateIds")
        if not quality.get("sourceShapeIds"):
            errors.append(f"{label}.figureQuality 缺少 sourceShapeIds")
        if not quality.get("cropMetadata"):
            errors.append(f"{label}.figureQuality 缺少 cropMetadata")
    semantic_review = quality.get("semanticReview")
    if not (
        isinstance(semantic_review, dict)
        and semantic_review.get("labelsVerified") is True
        and semantic_review.get("lineEndpointsVerified") is True
        and semantic_review.get("unrelatedContentExcluded") is True
        and semantic_review.get("localCropOpened") is True
    ):
        errors.append(f"{label}.figureQuality.semanticReview 未完整确认")
    review = quality.get("visualReview")
    if not (
        isinstance(review, dict)
        and review.get("status") == "passed"
        and review.get("reviewedAgainstPage") is True
    ):
        errors.append(f"{label}.figureQuality 尚未通过原页视觉复核")


def _safe_local_file(base: Path, raw: object, label: str, errors: list[str]) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        errors.append(f"{label} 缺少本地文件路径")
        return None
    if "://" in raw or raw.startswith("data:"):
        errors.append(f"{label} 不允许 URL 或 Base64：{raw[:80]}")
        return None
    candidate = Path(raw)
    resolved = candidate.resolve() if candidate.is_absolute() else (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        errors.append(f"{label} 必须位于分析目录内：{raw}")
        return None
    if not resolved.is_file():
        errors.append(f"{label} 文件不存在：{raw}")
        return None
    return resolved


def _validate_latex(value: str, label: str, errors: list[str]) -> None:
    unescaped_dollars = len(re.findall(r"(?<!\\)\$", value))
    if unescaped_dollars % 2:
        errors.append(f"{label} 的 $ 数量不成对")
    if value.count(r"\[") != value.count(r"\]"):
        errors.append(f"{label} 的 \\[...\\] 定界符不成对")


def _coalesce_inline_blocks(blocks: list[dict[str, str]]) -> list[dict[str, str]]:
    """把文字与相邻行内公式合成一段，避免 TEX 协议把公式渲染成独立段落。"""

    result: list[dict[str, str]] = []
    run: list[dict[str, str]] = []

    def flush() -> None:
        if not run:
            return
        if len(run) == 1:
            result.append(run[0])
        else:
            result.append(
                {
                    "kind": "text",
                    "value": "".join(str(block.get("value") or "") for block in run),
                }
            )
        run.clear()

    for block in blocks:
        if block.get("kind") == "image":
            flush()
            result.append(block)
            continue
        # 连续 text 块仍代表显式分段；只合并至少含一个 latex 块的相邻内容。
        if run and run[-1].get("kind") == "text" and block.get("kind") == "text":
            flush()
        run.append(block)
    flush()
    return result


def _fidelity_units(value: str) -> list[str]:
    """提取不受空白、标点和 LaTeX 命令影响的源文顺序单元。"""

    # NFKC 会把 3² 合并成 32；先插入脚本标记，才能与 LaTeX 的 3^2 等价比较。
    normalized = unicodedata.normalize("NFKC", value.translate(SCRIPT_DIGIT_TRANSLATION))
    normalized = re.sub(r"\\[A-Za-z]+", "", normalized)
    units = re.findall(r"[\u3400-\u9fff]|[A-Za-z]+|\d+(?:\.\d+)?", normalized)
    return [unit.lower() if unit.isascii() else unit for unit in units]


def _ordered_coverage(needle: list[str], haystack: list[str]) -> float:
    if not needle:
        return 0.0
    cursor = 0
    matched = 0
    for unit in needle:
        try:
            position = haystack.index(unit, cursor)
        except ValueError:
            continue
        matched += 1
        cursor = position + 1
    return matched / len(needle)


def _fidelity_score(source_text: str, generated_text: str) -> tuple[float, int, int]:
    source_units = _fidelity_units(source_text)
    generated_units = _fidelity_units(generated_text)
    if not source_units or not generated_units:
        return 0.0, len(source_units), len(generated_units)
    score = difflib.SequenceMatcher(
        a=source_units,
        b=generated_units,
        autojunk=False,
    ).ratio()
    return score, len(source_units), len(generated_units)


def _question_text(
    stem: list[dict[str, str]],
    options: dict[str, list[dict[str, str]]],
) -> str:
    stem_text = "".join(
        block.get("value", "") for block in stem if block.get("kind") != "image"
    )
    # 选项键由协议单独存储，但源文包含 A./B./C./D.；保真比对时必须补回键名。
    option_texts = [
        f"{key}. "
        + "".join(
            block.get("value", "")
            for block in options[key]
            if block.get("kind") != "image"
        )
        for key in sorted(options)
    ]
    return "\n".join([stem_text, *option_texts])


def _content_blocks(
    raw: object,
    label: str,
    question_ref: str,
    target: str,
    errors: list[str],
    image_uses: list[tuple[str, str, str]],
) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append(f"{label} 必须是内容块数组")
        return []
    result: list[dict[str, str]] = []
    for index, block in enumerate(raw):
        block_label = f"{label}[{index}]"
        if not isinstance(block, dict):
            errors.append(f"{block_label} 必须是对象")
            continue
        kind = block.get("kind")
        if kind in {"text", "latex"}:
            value = block.get("value")
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{block_label} 缺少非空 value")
                continue
            if kind == "latex":
                _validate_latex(value, block_label, errors)
            result.append({"kind": str(kind), "value": value})
        elif kind == "image":
            asset_id = block.get("assetId")
            if not isinstance(asset_id, str) or not ASSET_ID_RE.fullmatch(asset_id):
                errors.append(f"{block_label} 的 assetId 非法")
                continue
            image_uses.append((asset_id, question_ref, target))
            result.append({"kind": "image", "assetId": asset_id})
        else:
            errors.append(f"{block_label} 的 kind 必须是 text、latex 或 image")
    return _coalesce_inline_blocks(result)


def build(analysis_path: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    base = analysis_path.parent.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    data = _read_json(analysis_path)

    if data.get("protocol") != PROTOCOL:
        errors.append(f"protocol 必须是 {PROTOCOL}")
    if data.get("version") != 1:
        errors.append("version 必须是 1")

    source = data.get("source")
    if not isinstance(source, dict):
        errors.append("source 必须是对象")
        source = {}
    filename = source.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        errors.append("source.filename 不能为空")
        filename = "source.pptx"
    page_count = source.get("pageCount")
    if not isinstance(page_count, int) or isinstance(page_count, bool) or page_count < 1:
        errors.append("source.pageCount 必须是正整数")
        page_count = 0

    recognition_evidence_path = _safe_local_file(
        base,
        data.get("recognitionEvidence"),
        "recognitionEvidence",
        errors,
    )
    recognition_blocks: dict[tuple[int, str], str] = {}
    if recognition_evidence_path:
        try:
            recognition_evidence = _read_json(recognition_evidence_path)
            if (
                recognition_evidence.get("protocol")
                != "qulifang-ppt-recognition-evidence"
                or recognition_evidence.get("version") != 1
            ):
                errors.append(
                    "recognitionEvidence 协议必须是 qulifang-ppt-recognition-evidence/v1"
                )
            if recognition_evidence.get("pageCount") != page_count:
                errors.append("recognitionEvidence.pageCount 与 source.pageCount 不一致")
            evidence_sha = str(recognition_evidence.get("sourceSha256") or "")
            source_sha = str(source.get("sha256") or "")
            if source_sha and evidence_sha != source_sha:
                errors.append("recognitionEvidence.sourceSha256 与 source.sha256 不一致")
            for evidence_page in recognition_evidence.get("pages") or []:
                if not isinstance(evidence_page, dict):
                    continue
                evidence_page_no = evidence_page.get("pageNo")
                if not isinstance(evidence_page_no, int):
                    continue
                for block in evidence_page.get("textBlocks") or []:
                    if not isinstance(block, dict):
                        continue
                    shape_id = str(block.get("shapeId") or "")
                    block_text = block.get("text")
                    if shape_id and isinstance(block_text, str):
                        recognition_blocks[(evidence_page_no, shape_id)] = block_text
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"recognitionEvidence 无法读取：{exc}")

    pages = data.get("pages")
    if not isinstance(pages, list):
        errors.append("pages 必须是数组")
        pages = []

    page_numbers: list[int] = []
    normalized_questions: list[dict[str, Any]] = []
    question_ref_to_number: dict[str, str] = {}
    question_ref_to_page: dict[str, int] = {}
    image_uses: list[tuple[str, str, str]] = []
    lecture_count = 0
    question_page_count = 0
    multi_question_page_count = 0
    fidelity_details: list[dict[str, Any]] = []

    sortable_pages: list[tuple[int, dict[str, Any]]] = []
    for page_position, page in enumerate(pages):
        if not isinstance(page, dict):
            errors.append(f"pages[{page_position}] 必须是对象")
            continue
        page_no = page.get("pageNo")
        if not isinstance(page_no, int) or isinstance(page_no, bool) or page_no < 1:
            errors.append(f"pages[{page_position}].pageNo 必须是正整数")
            continue
        page_numbers.append(page_no)
        sortable_pages.append((page_no, page))

    if page_count and sorted(page_numbers) != list(range(1, page_count + 1)):
        errors.append(f"pages 必须完整覆盖 1..{page_count}，且页码不能重复")

    for page_no, page in sorted(sortable_pages, key=lambda item: item[0]):
        page_type = page.get("pageType")
        questions = page.get("questions")
        if not isinstance(questions, list):
            errors.append(f"第 {page_no} 页 questions 必须是数组")
            questions = []
        page_image = _safe_local_file(
            base, page.get("pageImage"), f"第 {page_no} 页 pageImage", errors
        )
        if page_image and _image_type(page_image) is None:
            errors.append(f"第 {page_no} 页 pageImage 必须是 PNG、JPG 或 WEBP")

        if page_type == "lecture":
            lecture_count += 1
            if questions:
                errors.append(f"第 {page_no} 页是 lecture，questions 必须为空")
            continue
        if page_type != "question":
            errors.append(f"第 {page_no} 页 pageType 必须是 lecture 或 question")
            continue
        question_page_count += 1
        if not questions:
            errors.append(f"第 {page_no} 页是 question，但没有题目")
            continue

        if _validate_question_grouping(page_no, page, questions, errors):
            multi_question_page_count += 1

        item_indexes = [question.get("itemIndex") for question in questions if isinstance(question, dict)]
        if item_indexes != list(range(len(questions))):
            errors.append(f"第 {page_no} 页 itemIndex 必须从 0 连续递增")

        for position, question in enumerate(questions):
            label = f"第 {page_no} 页第 {position + 1} 题"
            if not isinstance(question, dict):
                errors.append(f"{label} 必须是对象")
                continue
            question_ref = question.get("ref")
            expected_ref = f"p{page_no:04d}-i{position:02d}"
            if question_ref != expected_ref or not QUESTION_REF_RE.fullmatch(str(question_ref or "")):
                errors.append(f"{label} ref 必须是 {expected_ref}")
                question_ref = expected_ref
            if question_ref in question_ref_to_number:
                errors.append(f"题目 ref 重复：{question_ref}")
                continue

            qtype_raw = str(question.get("type") or "")
            qtype = QTYPE_MAP.get(qtype_raw)
            if not qtype:
                errors.append(f"{label} 题型不支持：{qtype_raw or '空'}")
                qtype = "应用题"
            elif qtype != qtype_raw:
                warnings.append(f"{label} 题型已从“{qtype_raw}”归一为“{qtype}”")

            final_number = str(len(normalized_questions) + 1)
            question_ref_to_number[question_ref] = final_number
            question_ref_to_page[question_ref] = page_no

            stem = _content_blocks(
                question.get("stem"), f"{label}.stem", question_ref, "stem", errors, image_uses
            )
            if not stem:
                errors.append(f"{label} 题干不能为空")

            options_raw = question.get("options") or {}
            if not isinstance(options_raw, dict):
                errors.append(f"{label}.options 必须是对象")
                options_raw = {}
            options: dict[str, list[dict[str, str]]] = {}
            for key, value in options_raw.items():
                if key not in {"A", "B", "C", "D"}:
                    errors.append(f"{label} 包含非法选项：{key}")
                    continue
                options[key] = _content_blocks(
                    value,
                    f"{label}.options.{key}",
                    question_ref,
                    f"option:{key}",
                    errors,
                    image_uses,
                )
            if qtype == "选择题" and set(options) != {"A", "B", "C", "D"}:
                warnings.append(f"{label} 选择题未完整包含 A–D，需在审核页确认")

            source_text = question.get("sourceText")
            source_text_origin = question.get("sourceTextOrigin")
            if not isinstance(source_text, str) or not source_text.strip():
                errors.append(f"{label}.sourceText 必须逐字保存题目区域的源文")
                source_text = ""
            if source_text_origin not in {"native", "visual"}:
                errors.append(f"{label}.sourceTextOrigin 必须是 native 或 visual")
                source_text_origin = "visual"

            source_evidence = question.get("sourceEvidence") or []
            if not isinstance(source_evidence, list):
                errors.append(f"{label}.sourceEvidence 必须是数组")
                source_evidence = []
            if source_text_origin == "native":
                if not source_evidence:
                    errors.append(f"{label} 的 native sourceText 必须包含 sourceEvidence")
                evidence_slices: list[str] = []
                for evidence_index, evidence in enumerate(source_evidence):
                    evidence_label = f"{label}.sourceEvidence[{evidence_index}]"
                    if not isinstance(evidence, dict):
                        errors.append(f"{evidence_label} 必须是对象")
                        continue
                    shape_id = str(evidence.get("shapeId") or "")
                    block_text = recognition_blocks.get((page_no, shape_id))
                    start = evidence.get("start")
                    end = evidence.get("end")
                    if block_text is None:
                        errors.append(f"{evidence_label}.shapeId 不在识别证据中：{shape_id}")
                        continue
                    if (
                        not isinstance(start, int)
                        or isinstance(start, bool)
                        or not isinstance(end, int)
                        or isinstance(end, bool)
                        or not 0 <= start < end <= len(block_text)
                    ):
                        errors.append(f"{evidence_label} 的 start/end 超出文本框范围")
                        continue
                    evidence_slices.append(block_text[start:end])
                if evidence_slices and source_text != "\n".join(evidence_slices):
                    errors.append(f"{label}.sourceText 必须与 sourceEvidence 字符区间逐字一致")
            elif source_evidence:
                errors.append(f"{label} 的 visual sourceText 不应伪造 OOXML sourceEvidence")

            text_review = question.get("textReview")
            if not (
                isinstance(text_review, dict)
                and text_review.get("status") == "passed"
                and text_review.get("reviewedAgainstPage") is True
                and text_review.get("formulaChecked") is True
                and text_review.get("optionOrderChecked") is True
                and int(text_review.get("reviewPasses") or 0) >= 2
            ):
                errors.append(f"{label}.textReview 必须完成至少两遍原页文字、公式和选项复核")

            generated_text = _question_text(stem, options)
            fidelity_score, source_unit_count, generated_unit_count = _fidelity_score(
                source_text,
                generated_text,
            )
            native_trace_score: float | None = None
            native_text = page.get("nativeText")
            if source_text_origin == "native":
                if not isinstance(native_text, str) or not native_text.strip():
                    errors.append(f"{label} 标记为 native 源文，但页面没有 nativeText")
                    native_trace_score = 0.0
                else:
                    native_trace_score = _ordered_coverage(
                        _fidelity_units(source_text),
                        _fidelity_units(native_text),
                    )
                    if native_trace_score < NATIVE_TRACE_THRESHOLD:
                        errors.append(
                            f"{label}.sourceText 无法按顺序追溯到 nativeText "
                            f"（{native_trace_score:.1%} < {NATIVE_TRACE_THRESHOLD:.0%}）"
                        )
            if fidelity_score < SOURCE_FIDELITY_THRESHOLD:
                errors.append(
                    f"{label} 题干/选项与 sourceText 保真度不足 "
                    f"（{fidelity_score:.1%} < {SOURCE_FIDELITY_THRESHOLD:.0%}），禁止概括或改写"
                )
            fidelity_details.append(
                {
                    "questionRef": question_ref,
                    "sourceTextOrigin": source_text_origin,
                    "sourceEvidence": source_evidence,
                    "textReview": text_review,
                    "score": round(fidelity_score, 4),
                    "nativeTraceScore": (
                        round(native_trace_score, 4) if native_trace_score is not None else None
                    ),
                    "sourceUnitCount": source_unit_count,
                    "generatedUnitCount": generated_unit_count,
                }
            )

            metadata = question.get("metadata") or {}
            if not isinstance(metadata, dict):
                errors.append(f"{label}.metadata 必须是对象")
                metadata = {}
            normalized_questions.append(
                {
                    "number": final_number,
                    # 课件代码包用这三个字段把去重后的题目重新映射回原 PPT 页面。
                    "sourceRef": question_ref,
                    "sourcePage": page_no,
                    "sourceItemIndex": position,
                    "sourceText": source_text,
                    "sourceTextOrigin": source_text_origin,
                    "sourceFidelityScore": round(fidelity_score, 4),
                    "type": qtype,
                    "metadata": metadata,
                    "stem": stem,
                    "options": options,
                    "answer": _content_blocks(
                        question.get("answer"),
                        f"{label}.answer",
                        question_ref,
                        "answer",
                        errors,
                        image_uses,
                    ),
                    "analysis": _content_blocks(
                        question.get("analysis"),
                        f"{label}.analysis",
                        question_ref,
                        "analysis",
                        errors,
                        image_uses,
                    ),
                }
            )

    assets_raw = data.get("assets") or []
    if not isinstance(assets_raw, list):
        errors.append("assets 必须是数组")
        assets_raw = []
    assets_by_id: dict[str, dict[str, Any]] = {}
    asset_sources: dict[str, Path] = {}
    normalized_assets: list[dict[str, Any]] = []
    for position, asset in enumerate(assets_raw):
        label = f"assets[{position}]"
        if not isinstance(asset, dict):
            errors.append(f"{label} 必须是对象")
            continue
        asset_id = asset.get("id")
        if not isinstance(asset_id, str) or not ASSET_ID_RE.fullmatch(asset_id):
            errors.append(f"{label}.id 非法")
            continue
        if asset_id in assets_by_id:
            errors.append(f"图片 ID 重复：{asset_id}")
            continue
        question_ref = asset.get("questionRef")
        target = asset.get("target")
        if question_ref not in question_ref_to_number:
            errors.append(f"{label}.questionRef 不存在：{question_ref}")
        if target not in ALLOWED_TARGETS:
            errors.append(f"{label}.target 非法：{target}")
        source_path = _safe_local_file(base, asset.get("source"), f"{label}.source", errors)
        if source_path and _image_type(source_path) is None:
            errors.append(f"{label}.source 必须是 PNG、JPG 或 WEBP")
        _validate_figure_quality(asset, label, errors)
        source_page = asset.get("sourcePage")
        if question_ref in question_ref_to_page and source_page != question_ref_to_page[question_ref]:
            errors.append(
                f"{label}.sourcePage 必须是题目所在页 {question_ref_to_page[question_ref]}"
            )
        normalized = {
            "id": asset_id,
            "source": str(source_path) if source_path else str(asset.get("source") or ""),
            "questionNumber": question_ref_to_number.get(str(question_ref), ""),
            "target": target,
            "sourcePage": source_page,
            "sourceBbox": asset.get("sourceBbox"),
            "confidence": asset.get("confidence"),
        }
        assets_by_id[asset_id] = normalized
        if source_path:
            asset_sources[asset_id] = source_path
        normalized_assets.append(normalized)

    use_counts = Counter(asset_id for asset_id, _, _ in image_uses)
    for asset_id, question_ref, target in image_uses:
        asset = assets_by_id.get(asset_id)
        if not asset:
            errors.append(f"题目引用了未声明图片：{asset_id}")
            continue
        if asset["questionNumber"] != question_ref_to_number.get(question_ref):
            errors.append(f"图片 {asset_id} 的题目归属不一致")
        if asset["target"] != target:
            errors.append(f"图片 {asset_id} 的 target 应为 {target}")
    for asset_id in assets_by_id:
        count = use_counts.get(asset_id, 0)
        if count != 1:
            errors.append(f"图片 {asset_id} 必须且只能被引用一次，实际 {count} 次")

    if normalized_assets:
        figure_report_path = _safe_local_file(
            base,
            data.get("figureValidationReport"),
            "figureValidationReport",
            errors,
        )
        figure_report: dict[str, Any] = {}
        if figure_report_path:
            try:
                raw_report = json.loads(figure_report_path.read_text(encoding="utf-8"))
                if isinstance(raw_report, dict):
                    figure_report = raw_report
                else:
                    errors.append("figureValidationReport 顶层必须是对象")
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"figureValidationReport 无法读取：{exc}")
        if figure_report:
            if (
                figure_report.get("protocol") != "qulifang-ppt-figure-validation"
                or figure_report.get("version") != 1
            ):
                errors.append("figureValidationReport 协议必须是 qulifang-ppt-figure-validation/v1")
            if figure_report.get("ok") is not True:
                errors.append("figureValidationReport 尚未通过校验")
            quality_profile = figure_report.get("qualityProfile")
            if not isinstance(quality_profile, dict):
                errors.append("figureValidationReport 缺少高清 qualityProfile，请重新校验配图")
            else:
                if int(quality_profile.get("minimumSourceEffectiveDpi") or 0) < MIN_FIGURE_SOURCE_DPI:
                    errors.append("figureValidationReport 的源页面 DPI 门槛低于 360")
                if int(quality_profile.get("minimumShortEdge") or 0) < MIN_FIGURE_SHORT_EDGE:
                    errors.append("figureValidationReport 的配图短边门槛低于 600 px")
                if int(quality_profile.get("minimumLongEdge") or 0) < MIN_FIGURE_LONG_EDGE:
                    errors.append("figureValidationReport 的配图长边门槛低于 800 px")
                if quality_profile.get("interpolationUpscalingAccepted") is not False:
                    errors.append("figureValidationReport 不得接受插值放大作为高清来源")
            expected_ids = set(assets_by_id)
            reported_ids = set(str(value) for value in figure_report.get("assetIds") or [])
            if reported_ids != expected_ids:
                errors.append("figureValidationReport 覆盖的图片 ID 与 assets 不一致")
            reported_hashes = figure_report.get("assetHashes") or {}
            if not isinstance(reported_hashes, dict):
                errors.append("figureValidationReport.assetHashes 必须是对象")
                reported_hashes = {}
            for asset_id, source_path in asset_sources.items():
                if reported_hashes.get(asset_id) != _sha256(source_path):
                    errors.append(f"图片 {asset_id} 在配图校验后发生变化，请重新校验")
            warnings.extend(str(item) for item in figure_report.get("warnings") or [])

    needs_review = data.get("needsReview") or []
    if not isinstance(needs_review, list):
        errors.append("needsReview 必须是数组")
        needs_review = []
    blocking_reviews = [
        item for item in needs_review if isinstance(item, dict) and item.get("blocking") is True
    ]
    if blocking_reviews:
        errors.append(f"存在 {len(blocking_reviews)} 个 blocking needsReview")

    source_warnings = data.get("warnings") or []
    if isinstance(source_warnings, list):
        warnings.extend(str(item) for item in source_warnings)
    else:
        errors.append("warnings 必须是数组")

    report = {
        "protocol": PROTOCOL,
        "version": 1,
        "ok": not errors,
        "pageCount": page_count,
        "lecturePageCount": lecture_count,
        "questionPageCount": question_page_count,
        "multiQuestionPageCount": multi_question_page_count,
        "questionCount": len(normalized_questions),
        "questionGrouping": {
            "policy": "one-question-per-page-default",
            "subquestionsRemainInSingleStem": True,
            "multipleQuestionPageCount": multi_question_page_count,
        },
        "assetCount": len(normalized_assets),
        "needsReviewCount": len(needs_review),
        "blockingReviewCount": len(blocking_reviews),
        "sourceFidelity": {
            "threshold": SOURCE_FIDELITY_THRESHOLD,
            "nativeTraceThreshold": NATIVE_TRACE_THRESHOLD,
            "checkedQuestionCount": len(fidelity_details),
            "passingQuestionCount": sum(
                detail["score"] >= SOURCE_FIDELITY_THRESHOLD
                and (
                    detail["nativeTraceScore"] is None
                    or detail["nativeTraceScore"] >= NATIVE_TRACE_THRESHOLD
                )
                for detail in fidelity_details
            ),
            "minimumScore": min(
                (detail["score"] for detail in fidelity_details),
                default=None,
            ),
            "details": fidelity_details,
        },
        "errors": errors,
        "warnings": warnings,
    }
    if errors:
        return None, report

    ir = {
        "title": str(data.get("title") or Path(str(filename)).stem),
        "source": {
            "filename": filename,
            "sha256": str(source.get("sha256") or ""),
        },
        "questions": normalized_questions,
        "assets": normalized_assets,
        "warnings": warnings,
        "needsReview": needs_review,
    }
    return ir, report


def main() -> int:
    parser = argparse.ArgumentParser(description="把 PPT 分析清单转换为标准题目 IR")
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    try:
        ir, report = build(args.analysis.resolve())
    except ValueError as exc:
        report = {
            "protocol": PROTOCOL,
            "version": 1,
            "ok": False,
            "errors": [str(exc)],
            "warnings": [],
        }
        _write_json(args.report.resolve(), report)
        parser.exit(1, f"错误：{exc}\n")

    _write_json(args.report.resolve(), report)
    if ir is None:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    _write_json(args.output.resolve(), ir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
