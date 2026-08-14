#!/usr/bin/env python3
"""安全解析 PPTX 的 OOXML 证据，并以可校验的高清方式渲染逐页 PNG。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import mimetypes
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from PIL import Image


PROTOCOL = "qulifang-ppt-tex-prepared"
MAX_ARCHIVE_FILES = 10_000
MAX_UNCOMPRESSED_BYTES = 1_024 * 1_024 * 1_024
EMU_PER_INCH = 914_400
DEFAULT_RENDER_DPI = 480
DEFAULT_MIN_EFFECTIVE_DPI = 360

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
REL_ID = f"{{{NS['r']}}}id"


class PreparationError(RuntimeError):
    """PPTX 无法被安全准备。"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_xml(archive: zipfile.ZipFile, member: str) -> ET.Element:
    try:
        return ET.fromstring(archive.read(member))
    except KeyError as exc:
        raise PreparationError(f"PPTX 缺少必要文件：{member}") from exc
    except ET.ParseError as exc:
        raise PreparationError(f"PPTX XML 无法解析：{member}: {exc}") from exc


def _safe_archive(archive: zipfile.ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise PreparationError(f"PPTX 内文件数超过限制：{len(infos)}")
    total = sum(info.file_size for info in infos)
    if total > MAX_UNCOMPRESSED_BYTES:
        raise PreparationError(f"PPTX 解压后超过 1GB：{total} bytes")
    for info in infos:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts:
            raise PreparationError(f"PPTX 包含不安全路径：{info.filename}")


def _relationship_map(root: ET.Element) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for rel in root.findall("rel:Relationship", NS):
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            result[rel_id] = {
                "target": target,
                "type": rel.attrib.get("Type", ""),
                "targetMode": rel.attrib.get("TargetMode", ""),
            }
    return result


def _resolve_member(owner: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(owner), target))


def _slide_members(archive: zipfile.ZipFile) -> list[str]:
    presentation = _read_xml(archive, "ppt/presentation.xml")
    relationships = _relationship_map(
        _read_xml(archive, "ppt/_rels/presentation.xml.rels")
    )
    members: list[str] = []
    for slide_id in presentation.findall(".//p:sldIdLst/p:sldId", NS):
        rel_id = slide_id.attrib.get(REL_ID)
        rel = relationships.get(rel_id or "")
        if not rel:
            raise PreparationError(f"幻灯片关系不存在：{rel_id}")
        member = _resolve_member("ppt/presentation.xml", rel["target"])
        if member not in archive.namelist():
            raise PreparationError(f"幻灯片 XML 不存在：{member}")
        members.append(member)
    if not members:
        raise PreparationError("PPTX 中没有幻灯片")
    return members


def _slide_size(archive: zipfile.ZipFile) -> dict[str, int]:
    presentation = _read_xml(archive, "ppt/presentation.xml")
    node = presentation.find("p:sldSz", NS)
    if node is None:
        raise PreparationError("PPTX 缺少幻灯片尺寸")
    try:
        cx = int(node.attrib["cx"])
        cy = int(node.attrib["cy"])
    except (KeyError, ValueError) as exc:
        raise PreparationError("PPTX 幻灯片尺寸无效") from exc
    if cx <= 0 or cy <= 0:
        raise PreparationError("PPTX 幻灯片尺寸必须为正数")
    return {"cx": cx, "cy": cy}


def _rels_member(owner: str) -> str:
    path = PurePosixPath(owner)
    return str(path.parent / "_rels" / f"{path.name}.rels")


def _text(root: ET.Element) -> str:
    values = [node.text or "" for node in root.findall(".//a:t", NS)]
    return "\n".join(value.strip() for value in values if value.strip())


def _shape_transform(shape: ET.Element) -> ET.Element | None:
    # 只读取当前顶层 Shape 的变换，避免误拿到组合图形内部子元素的坐标。
    for path in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm"):
        xfrm = shape.find(path, NS)
        if xfrm is not None:
            return xfrm
    return None


def _shape_box(shape: ET.Element) -> dict[str, int] | None:
    xfrm = _shape_transform(shape)
    if xfrm is None:
        return None
    offset = xfrm.find("a:off", NS)
    extent = xfrm.find("a:ext", NS)
    if offset is None or extent is None:
        return None
    try:
        x = int(offset.attrib.get("x", "0"))
        y = int(offset.attrib.get("y", "0"))
        cx = int(extent.attrib.get("cx", "0"))
        cy = int(extent.attrib.get("cy", "0"))
        rotation = int(xfrm.attrib.get("rot", "0")) / 60000.0
    except ValueError:
        return None

    # OOXML 直线常以 cy=0 加旋转表示；返回旋转后的轴对齐外接框，避免裁图漏边。
    radians = math.radians(rotation % 360.0)
    rotated_cx = abs(cx * math.cos(radians)) + abs(cy * math.sin(radians))
    rotated_cy = abs(cx * math.sin(radians)) + abs(cy * math.cos(radians))
    center_x = x + cx / 2.0
    center_y = y + cy / 2.0
    return {
        "x": round(center_x - rotated_cx / 2.0),
        "y": round(center_y - rotated_cy / 2.0),
        "cx": max(0, round(rotated_cx)),
        "cy": max(0, round(rotated_cy)),
    }


def _shape_metadata(shape: ET.Element, shape_index: int) -> dict[str, object]:
    c_nv_pr = next(
        (node for node in shape.iter() if _local_name(node.tag) == "cNvPr"),
        None,
    )
    rel_ids: list[str] = []
    for node in shape.iter():
        for attr, value in node.attrib.items():
            if attr.endswith("}embed") or attr.endswith("}link"):
                rel_ids.append(value)
    xfrm = _shape_transform(shape)
    try:
        rotation = int(xfrm.attrib.get("rot", "0")) / 60000.0 if xfrm is not None else 0.0
    except ValueError:
        rotation = 0.0
    return {
        "shapeIndex": shape_index,
        "shapeId": c_nv_pr.attrib.get("id", "") if c_nv_pr is not None else "",
        "kind": _local_name(shape.tag),
        "name": c_nv_pr.attrib.get("name", "") if c_nv_pr is not None else "",
        "description": c_nv_pr.attrib.get("descr", "") if c_nv_pr is not None else "",
        "text": _text(shape),
        "boxEmu": _shape_box(shape),
        "rotationDeg": rotation,
        "relationshipIds": list(dict.fromkeys(rel_ids)),
    }


def _slide_relationships(
    archive: zipfile.ZipFile, slide_member: str
) -> dict[str, dict[str, str]]:
    rels_member = _rels_member(slide_member)
    if rels_member not in archive.namelist():
        return {}
    return _relationship_map(_read_xml(archive, rels_member))


def _notes_text(
    archive: zipfile.ZipFile,
    slide_member: str,
    relationships: dict[str, dict[str, str]],
) -> str:
    for rel in relationships.values():
        if rel["type"].endswith("/notesSlide"):
            member = _resolve_member(slide_member, rel["target"])
            if member in archive.namelist():
                return _text(_read_xml(archive, member))
    return ""


def _linked_media(
    archive: zipfile.ZipFile,
    slide_member: str,
    relationships: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    names = set(archive.namelist())
    for rel_id, rel in relationships.items():
        if rel["targetMode"].lower() == "external":
            continue
        member = _resolve_member(slide_member, rel["target"])
        if member.startswith("ppt/media/") and member in names:
            result.append({"relationshipId": rel_id, "member": member})
    return result


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _prepare_output(output: Path, replace_existing: bool) -> None:
    output.mkdir(parents=True, exist_ok=True)
    existing = list(output.iterdir())
    if not existing:
        return
    marker = output / "source-info.json"
    valid_marker = False
    if marker.is_file():
        try:
            valid_marker = json.loads(marker.read_text(encoding="utf-8")).get("protocol") == PROTOCOL
        except (json.JSONDecodeError, OSError):
            valid_marker = False
    if not replace_existing or not valid_marker:
        raise PreparationError(
            f"输出目录非空或不是本 Skill 的准备目录：{output}；请使用空目录"
        )
    for name in ("source-info.json", "slides", "embedded-media", "pages", "rendered.pdf"):
        target = output / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def _find_soffice() -> str | None:
    candidates = [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).exists()), None)


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise PreparationError(f"命令执行失败：{command[0]}: {message}")


def _render_pdf(pdf: Path, output: Path, dpi: int, *, keep_pdf: bool = True) -> int:
    pdftoppm = shutil.which("pdftoppm")
    if not pdftoppm:
        raise PreparationError("缺少渲染依赖：pdftoppm")
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise PreparationError(f"请输入存在的 PDF 文件：{pdf}")

    pages_dir = output / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    rendered_pdf = output / "rendered.pdf"
    if keep_pdf and pdf.resolve() != rendered_pdf.resolve():
        shutil.copy2(pdf, rendered_pdf)
    source_pdf = rendered_pdf if keep_pdf else pdf
    _run(
        [
            pdftoppm,
            "-png",
            "-r",
            str(dpi),
            str(source_pdf),
            str(pages_dir / "page"),
        ]
    )

    generated = sorted(
        pages_dir.glob("page-*.png"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )
    for index, path in enumerate(generated, 1):
        target = pages_dir / f"page-{index:04d}.png"
        if path != target:
            path.replace(target)
    return len(generated)


def _render_libreoffice(source: Path, output: Path, dpi: int) -> int:
    soffice = _find_soffice()
    if not soffice:
        raise PreparationError("缺少渲染依赖：LibreOffice/soffice")

    with tempfile.TemporaryDirectory(prefix="ppt-render-", dir=output) as temporary:
        temp_dir = Path(temporary)
        _run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(temp_dir),
                str(source),
            ]
        )
        pdf_candidates = list(temp_dir.glob("*.pdf"))
        if len(pdf_candidates) != 1:
            raise PreparationError("LibreOffice 未生成唯一 PDF")
        return _render_pdf(pdf_candidates[0], output, dpi)


def _copy_external_pages(source_dir: Path, output: Path) -> int:
    if not source_dir.is_dir():
        raise PreparationError(f"外部渲染页目录不存在：{source_dir}")

    def page_sort_key(path: Path) -> tuple[int, str]:
        numbers = re.findall(r"\d+", path.stem)
        return (int(numbers[-1]) if numbers else 10**9, path.name)

    candidates = sorted(
        (
            path
            for path in source_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ),
        key=page_sort_key,
    )
    if not candidates:
        raise PreparationError(f"外部渲染页目录中没有 PNG/JPG/WEBP：{source_dir}")
    pages_dir = output / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(candidates, 1):
        suffix = ".jpg" if source.suffix.lower() == ".jpeg" else source.suffix.lower()
        shutil.copy2(source, pages_dir / f"page-{index:04d}{suffix}")
    return len(candidates)


def _render_metrics(output: Path, slide_size: dict[str, int]) -> dict[str, object]:
    pages = sorted(
        (path for path in (output / "pages").iterdir() if path.is_file()),
        key=lambda path: path.name,
    )
    width_inches = int(slide_size["cx"]) / EMU_PER_INCH
    height_inches = int(slide_size["cy"]) / EMU_PER_INCH
    records: list[dict[str, object]] = []
    for page_no, path in enumerate(pages, 1):
        with Image.open(path) as image:
            width, height = image.size
        dpi_x = width / width_inches
        dpi_y = height / height_inches
        records.append(
            {
                "pageNo": page_no,
                "path": str(path.relative_to(output)),
                "pixelSize": {"width": width, "height": height},
                "effectiveDpi": round(min(dpi_x, dpi_y), 2),
            }
        )
    effective = [float(record["effectiveDpi"]) for record in records]
    return {
        "minimumEffectiveDpi": min(effective) if effective else 0.0,
        "maximumEffectiveDpi": max(effective) if effective else 0.0,
        "pages": records,
    }


def _copy_embedded_media(archive: zipfile.ZipFile, output: Path) -> list[dict[str, object]]:
    media_dir = output / "embedded-media"
    media_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    for member in sorted(name for name in archive.namelist() if name.startswith("ppt/media/")):
        name = PurePosixPath(member).name
        target = media_dir / name
        target.write_bytes(archive.read(member))
        media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        records.append(
            {
                "member": member,
                "path": str(target.relative_to(output)),
                "mediaType": media_type,
                "sizeBytes": target.stat().st_size,
                "sha256": _sha256(target),
            }
        )
    return records


def prepare(
    source: Path,
    output: Path,
    renderer: str,
    dpi: int,
    min_effective_dpi: int,
    replace_existing: bool,
    pages_dir: Path | None,
    rendered_pdf: Path | None,
) -> dict[str, object]:
    if source.suffix.lower() != ".pptx" or not source.is_file():
        raise PreparationError(f"请输入存在的 .pptx 文件：{source}")
    if dpi < 96 or dpi > 600:
        raise PreparationError("DPI 必须在 96–600 之间")
    if min_effective_dpi < 96 or min_effective_dpi > 600:
        raise PreparationError("最小有效 DPI 必须在 96–600 之间")
    if pages_dir is not None and rendered_pdf is not None:
        raise PreparationError("--pages-dir 与 --rendered-pdf 不能同时使用")
    _prepare_output(output, replace_existing)

    warnings: list[str] = []
    slides_dir = output / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)

    try:
        archive = zipfile.ZipFile(source)
    except zipfile.BadZipFile as exc:
        raise PreparationError("文件不是有效 PPTX ZIP") from exc

    with archive:
        _safe_archive(archive)
        if "[Content_Types].xml" not in archive.namelist():
            raise PreparationError("PPTX 缺少 [Content_Types].xml")
        members = _slide_members(archive)
        slide_size = _slide_size(archive)
        media = _copy_embedded_media(archive, output)
        slide_summaries: list[dict[str, object]] = []
        for page_no, member in enumerate(members, 1):
            root = _read_xml(archive, member)
            relationships = _slide_relationships(archive, member)
            shape_tree = root.find(".//p:cSld/p:spTree", NS)
            shapes = [] if shape_tree is None else [
                _shape_metadata(child, shape_index)
                for shape_index, child in enumerate(list(shape_tree))
                if _local_name(child.tag) in {"sp", "pic", "graphicFrame", "grpSp", "cxnSp"}
            ]
            slide_record = {
                "pageNo": page_no,
                "member": member,
                "nativeText": _text(root),
                "notesText": _notes_text(archive, member, relationships),
                "slideSizeEmu": slide_size,
                "shapes": shapes,
                "linkedMedia": _linked_media(archive, member, relationships),
            }
            slide_path = slides_dir / f"slide-{page_no:04d}.json"
            _write_json(slide_path, slide_record)
            slide_summaries.append(
                {
                    "pageNo": page_no,
                    "record": str(slide_path.relative_to(output)),
                    "nativeTextLength": len(str(slide_record["nativeText"])),
                    "shapeCount": len(shapes),
                    "linkedMediaCount": len(slide_record["linkedMedia"]),
                }
            )

    rendered_count = 0
    renderer_used = "none"
    if pages_dir is not None:
        if renderer != "none":
            raise PreparationError("使用 --pages-dir 时，--renderer 必须是 none")
        rendered_count = _copy_external_pages(pages_dir, output)
        renderer_used = "external"
    elif rendered_pdf is not None:
        if renderer != "none":
            raise PreparationError("使用 --rendered-pdf 时，--renderer 必须是 none")
        rendered_count = _render_pdf(rendered_pdf, output, dpi)
        renderer_used = "external-pdf"
    elif renderer in {"auto", "libreoffice"}:
        try:
            rendered_count = _render_libreoffice(source, output, dpi)
            renderer_used = "libreoffice"
        except PreparationError as exc:
            if renderer == "libreoffice":
                raise
            warnings.append(str(exc))
            warnings.append("未自动渲染页面；请使用 Codex presentation renderer 补齐 pages/")

    if rendered_count and rendered_count != len(slide_summaries):
        raise PreparationError(
            f"渲染页数 {rendered_count} 与 PPT 幻灯片数 {len(slide_summaries)} 不一致"
        )
    render_metrics = _render_metrics(output, slide_size) if rendered_count else None
    if (
        render_metrics is not None
        and float(render_metrics["minimumEffectiveDpi"]) < min_effective_dpi
    ):
        raise PreparationError(
            "渲染页清晰度不足："
            f"最低 {render_metrics['minimumEffectiveDpi']} DPI，"
            f"要求至少 {min_effective_dpi} DPI；请从 PowerPoint 原生导出 PDF 后以更高 DPI 重渲染"
        )

    result: dict[str, object] = {
        "protocol": PROTOCOL,
        "version": 1,
        "source": {
            "filename": source.name,
            "absolutePath": str(source.resolve()),
            "sizeBytes": source.stat().st_size,
            "sha256": _sha256(source),
        },
        "slideCount": len(slide_summaries),
        "slideSizeEmu": slide_size,
        "renderedPageCount": rendered_count,
        "renderer": renderer_used,
        "dpi": dpi if renderer_used in {"libreoffice", "external-pdf"} else None,
        "minimumRequiredEffectiveDpi": min_effective_dpi,
        "renderMetrics": render_metrics,
        "slides": slide_summaries,
        "embeddedMedia": media,
        "warnings": warnings,
    }
    _write_json(output / "source-info.json", result)
    return result


def _check() -> int:
    payload = {
        "soffice": _find_soffice(),
        "pdftoppm": shutil.which("pdftoppm"),
        "structureParser": "available",
        "defaultRenderDpi": DEFAULT_RENDER_DPI,
        "defaultMinimumEffectiveDpi": DEFAULT_MIN_EFFECTIVE_DPI,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="准备 PPTX 的结构证据与逐页图片")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--renderer", choices=("none", "auto", "libreoffice"), default="auto")
    parser.add_argument(
        "--dpi",
        type=int,
        default=DEFAULT_RENDER_DPI,
        help="PDF 转逐页 PNG 的渲染 DPI；默认 480，适合高清题目配图",
    )
    parser.add_argument(
        "--min-effective-dpi",
        type=int,
        default=DEFAULT_MIN_EFFECTIVE_DPI,
        help="逐页图片最低有效 DPI；默认 360，低于该值会阻止后续裁图",
    )
    parser.add_argument(
        "--pages-dir",
        type=Path,
        help="Codex/PowerPoint 已渲染的页面目录；复制并规范命名后登记为 external renderer",
    )
    parser.add_argument(
        "--rendered-pdf",
        type=Path,
        help="优先使用 PowerPoint 原生导出的 PDF，并按 --dpi 高清渲染逐页 PNG",
    )
    parser.add_argument("--replace-existing", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    if args.check:
        return _check()
    if args.input is None or args.output is None:
        parser.error("除 --check 外，必须同时提供 --input 和 --output")
    try:
        result = prepare(
            args.input.resolve(),
            args.output.resolve(),
            args.renderer,
            args.dpi,
            args.min_effective_dpi,
            args.replace_existing,
            args.pages_dir.resolve() if args.pages_dir else None,
            args.rendered_pdf.resolve() if args.rendered_pdf else None,
        )
    except PreparationError as exc:
        parser.exit(1, f"错误：{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
