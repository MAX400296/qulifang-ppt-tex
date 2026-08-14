from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import build_recognition_evidence  # noqa: E402
import extract_figure_candidates  # noqa: E402
import prepare_pptx  # noqa: E402


class RecognitionEvidenceTest(unittest.TestCase):
    def test_named_question_text_is_preserved_verbatim(self) -> None:
        source_text = "下图是 Garfield 证明勾股定理所用的图形：\n请写出证明过程。"
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "slides").mkdir()
            (work / "source-info.json").write_text(
                json.dumps(
                    {
                        "slideCount": 1,
                        "source": {"filename": "test.pptx", "sha256": "abc"},
                    }
                ),
                encoding="utf-8",
            )
            (work / "slides" / "slide-0001.json").write_text(
                json.dumps(
                    {
                        "pageNo": 1,
                        "nativeText": f"例题\n{source_text}\n页脚",
                        "slideSizeEmu": {"cx": 1000, "cy": 600},
                        "shapes": [
                            {
                                "shapeId": "2",
                                "shapeIndex": 2,
                                "name": "question-label-text",
                                "text": "例1｜认识勾股定理",
                                "boxEmu": {"x": 20, "y": 20, "cx": 200, "cy": 30},
                            },
                            {
                                "shapeId": "6",
                                "shapeIndex": 6,
                                "name": "question-text",
                                "text": source_text,
                                "boxEmu": {"x": 50, "y": 100, "cx": 700, "cy": 180},
                            },
                            {
                                "shapeId": "3",
                                "shapeIndex": 3,
                                "name": "footer-page",
                                "text": "1",
                                "boxEmu": {"x": 900, "y": 560, "cx": 30, "cy": 20},
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = build_recognition_evidence.build(work)

        page = result["pages"][0]
        self.assertEqual(page["questionTextCandidateShapeIds"], ["6"])
        self.assertEqual(page["textBlocks"][0]["text"], source_text)
        self.assertNotIn("2", [block["shapeId"] for block in page["textBlocks"]])
        self.assertNotIn("3", [block["shapeId"] for block in page["textBlocks"]])


class FigureCandidateTest(unittest.TestCase):
    def test_nearby_short_label_is_added_but_question_title_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "page.png"
            image = Image.new("RGB", (800, 450), "#24443b")
            draw = ImageDraw.Draw(image)
            draw.line((300, 160, 430, 300), fill="white", width=4)
            draw.text((441, 292), "A", fill="white")
            image.save(image_path)
            slide = {
                "pageNo": 1,
                "slideSizeEmu": {"cx": 8000, "cy": 4500},
                "shapes": [
                    {
                        "shapeId": "line-1",
                        "shapeIndex": 1,
                        "kind": "sp",
                        "name": "",
                        "text": "",
                        "boxEmu": {"x": 3000, "y": 1600, "cx": 1300, "cy": 1400},
                    },
                    {
                        "shapeId": "label-a",
                        "shapeIndex": 2,
                        "kind": "sp",
                        "name": "",
                        "text": "A",
                        "boxEmu": {"x": 4410, "y": 2900, "cx": 180, "cy": 160},
                    },
                    {
                        "shapeId": "title",
                        "shapeIndex": 3,
                        "kind": "sp",
                        "name": "question-label-text",
                        "text": "例1｜认识勾股定理",
                        "boxEmu": {"x": 2500, "y": 900, "cx": 2100, "cy": 250},
                    },
                ],
            }

            record, _ = extract_figure_candidates.build_page_candidates(
                slide,
                image_path,
                gap_ratio=0.01,
                min_area_ratio=0.0001,
            )

        self.assertEqual(record["candidateCount"], 1)
        candidate = record["candidates"][0]
        self.assertIn("label-a", candidate["shapeIds"])
        self.assertIn("label-a", candidate["autoIncludedLabelShapeIds"])
        self.assertIn("A", candidate["includedTextLabels"])
        self.assertNotIn("title", candidate["shapeIds"])

    def test_label_already_anchored_to_one_figure_is_not_copied_to_neighbor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_path = root / "page.png"
            Image.new("RGB", (800, 450), "white").save(image_path)
            slide = {
                "pageNo": 1,
                "slideSizeEmu": {"cx": 8000, "cy": 4500},
                "shapes": [
                    {
                        "shapeId": "left-line",
                        "shapeIndex": 1,
                        "kind": "sp",
                        "name": "",
                        "text": "",
                        "boxEmu": {"x": 2000, "y": 1500, "cx": 1200, "cy": 1300},
                    },
                    {
                        "shapeId": "left-a",
                        "shapeIndex": 2,
                        "kind": "sp",
                        "name": "",
                        "text": "A",
                        "boxEmu": {"x": 3150, "y": 2650, "cx": 150, "cy": 140},
                    },
                    {
                        "shapeId": "right-line",
                        "shapeIndex": 3,
                        "kind": "sp",
                        "name": "",
                        "text": "",
                        "boxEmu": {"x": 3400, "y": 1500, "cx": 1200, "cy": 1300},
                    },
                ],
            }

            record, _ = extract_figure_candidates.build_page_candidates(
                slide,
                image_path,
                gap_ratio=0.01,
                min_area_ratio=0.0001,
            )

        owners = [
            candidate["id"]
            for candidate in record["candidates"]
            if "left-a" in candidate["shapeIds"]
        ]
        self.assertEqual(len(owners), 1)


class FigureValidationTest(unittest.TestCase):
    def test_shape_candidate_render_keeps_all_pixels_and_passes_trace_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page_path = root / "page.png"
            # 以 480 DPI 页面量级验证默认高清门槛，避免用插值放大掩盖源图不足。
            page = Image.new("RGB", (2700, 1500), "#24443b")
            draw = ImageDraw.Draw(page)
            draw.line((780, 420, 1560, 990), fill="white", width=15)
            draw.text((1590, 945), "A", fill="white")
            page.save(page_path)
            candidate_path = root / "figure-candidates.json"
            candidate_path.write_text(
                json.dumps(
                    {
                        "protocol": "qulifang-ppt-figure-candidates",
                        "version": 1,
                        "pages": [
                            {
                                "pageNo": 1,
                                "pageImage": str(page_path),
                                "effectiveDpi": 480,
                                "candidates": [
                                    {
                                        "id": "p0001-c001",
                                        "bboxPx": [750, 390, 1665, 1035],
                                        "shapeIds": ["line", "label-a"],
                                        "autoIncludedLabelShapeIds": ["label-a"],
                                        "includedTextLabels": ["A"],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            crop_path = root / "crop.png"
            metadata_path = root / "crop.figure.json"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "render_figure_white.py"),
                    "--candidates",
                    str(candidate_path),
                    "--page",
                    "1",
                    "--candidate",
                    "p0001-c001",
                    "--asset-id",
                    "img_p0001_i00_stem_01",
                    "--output",
                    str(crop_path),
                    "--metadata-output",
                    str(metadata_path),
                    "--review-output",
                    str(root / "review.png"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(metadata["renderCoverageRatio"], 0.995)
            self.assertEqual(metadata["qualityTier"], "high-definition")
            self.assertGreaterEqual(min(metadata["outputSize"].values()), 600)
            analysis_path = root / "ppt-analysis.json"
            analysis_path.write_text(
                json.dumps(
                    {
                        "figureCandidates": candidate_path.name,
                        "assets": [
                            {
                                "id": "img_p0001_i00_stem_01",
                                "source": crop_path.name,
                                "figureQuality": {
                                    "cropMethod": "shape-candidates",
                                    "candidateIds": ["p0001-c001"],
                                    "sourceShapeIds": ["line", "label-a"],
                                    "includedTextLabels": ["A"],
                                    "cropMetadata": metadata_path.name,
                                    "paddingRatio": 0.04,
                                    "background": "white",
                                    "semanticReview": {
                                        "labelsVerified": True,
                                        "lineEndpointsVerified": True,
                                        "unrelatedContentExcluded": True,
                                        "localCropOpened": True,
                                    },
                                    "visualReview": {
                                        "status": "passed",
                                        "reviewedAgainstPage": True,
                                    },
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "validate_figure_assets.py"),
                    "--analysis",
                    str(analysis_path),
                    "--report",
                    str(root / "report.json"),
                    "--contact-sheet",
                    str(root / "contact.jpg"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_low_resolution_crop_is_rejected_without_interpolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page_path = root / "page.png"
            page = Image.new("RGB", (900, 500), "white")
            draw = ImageDraw.Draw(page)
            draw.rectangle((250, 150, 500, 330), outline="black", width=4)
            page.save(page_path)
            candidate_path = root / "figure-candidates.json"
            candidate_path.write_text(
                json.dumps(
                    {
                        "pages": [
                            {
                                "pageNo": 1,
                                "pageImage": str(page_path),
                                "effectiveDpi": 240,
                                "candidates": [
                                    {
                                        "id": "p0001-c001",
                                        "bboxPx": [240, 140, 510, 340],
                                        "shapeIds": ["shape-1"],
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "render_figure_white.py"),
                    "--candidates",
                    str(candidate_path),
                    "--page",
                    "1",
                    "--candidate",
                    "p0001-c001",
                    "--asset-id",
                    "img_p0001_i00_stem_01",
                    "--output",
                    str(root / "crop.png"),
                    "--metadata-output",
                    str(root / "crop.figure.json"),
                    "--review-output",
                    str(root / "review.png"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("配图清晰度不足", completed.stderr)


class RenderMetricsTest(unittest.TestCase):
    def test_effective_dpi_is_measured_from_slide_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pages").mkdir()
            Image.new("RGB", (480, 270), "white").save(root / "pages" / "page-0001.png")

            metrics = prepare_pptx._render_metrics(
                root,
                {"cx": 914_400, "cy": 514_350},
            )

        self.assertEqual(metrics["minimumEffectiveDpi"], 480.0)
        self.assertEqual(metrics["pages"][0]["pixelSize"], {"width": 480, "height": 270})


if __name__ == "__main__":
    unittest.main()
