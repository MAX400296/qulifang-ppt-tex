from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS_DIR))

import build_question_ir  # noqa: E402


class BuildQuestionIrTest(unittest.TestCase):
    def _analysis(
        self,
        root: Path,
        *,
        source_text: str,
        native_text: str,
        stem: list[dict[str, str]],
        source_origin: str = "native",
    ) -> Path:
        page_image = root / "page.png"
        page_image.write_bytes(b"\x89PNG\r\n\x1a\n")
        evidence = {
            "protocol": "qulifang-ppt-recognition-evidence",
            "version": 1,
            "sourceSha256": "test-sha",
            "pageCount": 1,
            "pages": [
                {
                    "pageNo": 1,
                    "textBlocks": [
                        {
                            "shapeId": "6",
                            "text": source_text,
                        }
                    ],
                }
            ],
        }
        evidence_path = root / "recognition-evidence.json"
        evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
        analysis = {
            "protocol": "qulifang-ppt-tex",
            "version": 1,
            "title": "测试",
            "source": {"filename": "test.pptx", "sha256": "test-sha", "pageCount": 1},
            "recognitionEvidence": "recognition-evidence.json",
            "pages": [
                {
                    "pageNo": 1,
                    "pageType": "question",
                    "pageImage": "page.png",
                    "nativeText": native_text,
                    "questions": [
                        {
                            "ref": "p0001-i00",
                            "itemIndex": 0,
                            "sourceText": source_text,
                            "sourceTextOrigin": source_origin,
                            "sourceEvidence": (
                                [{"shapeId": "6", "start": 0, "end": len(source_text)}]
                                if source_origin == "native"
                                else []
                            ),
                            "textReview": {
                                "status": "passed",
                                "reviewedAgainstPage": True,
                                "formulaChecked": True,
                                "optionOrderChecked": True,
                                "reviewPasses": 2,
                            },
                            "type": "应用题",
                            "metadata": {},
                            "stem": stem,
                            "options": {},
                            "answer": [],
                            "analysis": [],
                        }
                    ],
                }
            ],
            "assets": [],
            "warnings": [],
            "needsReview": [],
        }
        path = root / "ppt-analysis.json"
        path.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        return path

    def test_inline_latex_is_kept_inside_the_sentence(self) -> None:
        source = "以 a、b 为直角边，以 c 为斜边作两个全等直角三角形。"
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text=f"标题\n{source}\n页脚",
                stem=[
                    {"kind": "text", "value": "以 "},
                    {"kind": "latex", "value": "$a,b$"},
                    {"kind": "text", "value": " 为直角边，以 "},
                    {"kind": "latex", "value": "$c$"},
                    {"kind": "text", "value": " 为斜边作两个全等直角三角形。"},
                ],
            )
            ir, report = build_question_ir.build(path)

        self.assertTrue(report["ok"], report["errors"])
        self.assertIsNotNone(ir)
        self.assertEqual(
            ir["questions"][0]["stem"],
            [
                {
                    "kind": "text",
                    "value": "以 $a,b$ 为直角边，以 $c$ 为斜边作两个全等直角三角形。",
                }
            ],
        )
        self.assertEqual(report["sourceFidelity"]["checkedQuestionCount"], 1)

    def test_choice_fidelity_counts_protocol_option_keys(self) -> None:
        source = "勾股数是（  ）\nA. 3，4，5      B. 1，2，3      C. 2，3，4      D. 4，5，6"
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text=f"标题\n{source}\n页脚",
                stem=[{"kind": "text", "value": "勾股数是（  ）"}],
            )
            analysis = json.loads(path.read_text(encoding="utf-8"))
            question = analysis["pages"][0]["questions"][0]
            question["type"] = "选择题"
            question["options"] = {
                "A": [{"kind": "text", "value": "3，4，5"}],
                "B": [{"kind": "text", "value": "1，2，3"}],
                "C": [{"kind": "text", "value": "2，3，4"}],
                "D": [{"kind": "text", "value": "4，5，6"}],
            }
            path.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
            ir, report = build_question_ir.build(path)

        self.assertTrue(report["ok"], report["errors"])
        self.assertIsNotNone(ir)
        self.assertEqual(ir["questions"][0]["options"]["A"][0]["value"], "3，4，5")

    def test_unicode_superscript_matches_latex_exponent(self) -> None:
        source = "证明：3²+4²=5²。"
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text=f"标题\n{source}\n页脚",
                stem=[
                    {"kind": "text", "value": "证明："},
                    {"kind": "latex", "value": "$3^2+4^2=5^2$"},
                    {"kind": "text", "value": "。"},
                ],
            )
            ir, report = build_question_ir.build(path)

        self.assertTrue(report["ok"], report["errors"])
        self.assertIsNotNone(ir)
        self.assertEqual(report["sourceFidelity"]["minimumScore"], 1.0)

    def test_question_package_keeps_inline_latex_on_one_protocol_line(self) -> None:
        source = "以 a、b 为直角边，以 c 为斜边作两个全等直角三角形。"
        sibling_builder = (
            Path(__file__).resolve().parents[3]
            / "qulifang-to-tex"
            / "scripts"
            / "build_package.py"
        )
        self.assertTrue(sibling_builder.is_file(), "缺少 qulifang-to-tex builder")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = self._analysis(
                root,
                source_text=source,
                native_text=f"标题\n{source}\n页脚",
                stem=[
                    {"kind": "text", "value": "以 "},
                    {"kind": "latex", "value": "$a,b$"},
                    {"kind": "text", "value": " 为直角边，以 "},
                    {"kind": "latex", "value": "$c$"},
                    {"kind": "text", "value": " 为斜边作两个全等直角三角形。"},
                ],
            )
            ir, report = build_question_ir.build(path)
            self.assertTrue(report["ok"], report["errors"])
            ir_path = root / "questions.ir.json"
            ir_path.write_text(json.dumps(ir, ensure_ascii=False), encoding="utf-8")
            output = root / "question-package"
            subprocess.run(
                [
                    sys.executable,
                    str(sibling_builder),
                    "--ir",
                    str(ir_path),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            protocol = (output / "questions.tex").read_text(encoding="utf-8")

        self.assertIn("以 $a,b$ 为直角边，以 $c$ 为斜边作两个全等直角三角形。", protocol)
        self.assertNotIn("\n\n$a,b$\n\n", protocol)

    def test_paraphrased_question_is_blocked(self) -> None:
        source = (
            "勾股定理是一个基本的平面几何定理，也是数学中最重要的定理之一。"
            "下图是1876年美国总统 Garfield 证明勾股定理所用的图形。"
            "你能利用该图证明勾股定理吗？写出你的证明过程。"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text=f"例题\n{source}",
                stem=[{"kind": "text", "value": "利用该图证明勾股定理，写出证明过程。"}],
            )
            ir, report = build_question_ir.build(path)

        self.assertIsNone(ir)
        self.assertFalse(report["ok"])
        self.assertTrue(any("保真度不足" in error for error in report["errors"]))

    def test_garfield_page_keeps_the_complete_question_context(self) -> None:
        source = (
            "勾股定理是一个基本的平面几何定理，也是数学中最重要的定理之一。"
            "勾股定理其实有很多种方式证明。"
            "下图是1876年美国总统 Garfield 证明勾股定理所用的图形：\n"
            "以 a、b 为直角边，以 c 为斜边作两个全等的直角三角形（图1），"
            "把这两个直角三角形拼成如图2所示梯形形状。\n"
            "你能利用该图证明勾股定理吗？写出你的证明过程。"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text=f"例1｜认识勾股定理\n{source}\nb\na\nc\n图1",
                stem=[
                    {"kind": "text", "value": source.split("a、b", 1)[0] + ""},
                    {"kind": "latex", "value": "$a,b$"},
                    {"kind": "text", "value": source.split("a、b", 1)[1].split("c", 1)[0]},
                    {"kind": "latex", "value": "$c$"},
                    {"kind": "text", "value": source.split("a、b", 1)[1].split("c", 1)[1]},
                ],
            )
            ir, report = build_question_ir.build(path)

        self.assertTrue(report["ok"], report["errors"])
        self.assertIsNotNone(ir)
        self.assertIn("Garfield", ir["questions"][0]["stem"][0]["value"])
        self.assertIn("$a,b$", ir["questions"][0]["stem"][0]["value"])
        self.assertEqual(report["sourceFidelity"]["minimumScore"], 1.0)

    def test_native_source_text_must_be_traceable(self) -> None:
        source = "求三角形的面积。"
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text="完全不同的页面文字",
                stem=[{"kind": "text", "value": source}],
            )
            ir, report = build_question_ir.build(path)

        self.assertIsNone(ir)
        self.assertTrue(any("无法按顺序追溯" in error for error in report["errors"]))

    def test_visual_source_text_can_skip_native_trace(self) -> None:
        source = "根据图片计算阴影部分面积。"
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text="",
                source_origin="visual",
                stem=[{"kind": "text", "value": source}],
            )
            ir, report = build_question_ir.build(path)

        self.assertTrue(report["ok"], report["errors"])
        self.assertIsNotNone(ir)

    def test_numbered_subquestions_on_one_slide_cannot_be_split(self) -> None:
        source = (
            "在△ABC中，∠C=90°，AB=c，BC=a，AC=b。\n"
            "（1）若a=3，b=4，则c=____；\n"
            "（2）若a=24，c=25，则b=____；\n"
            "（3）若a=1.2，b=1.6，则c=____；\n"
            "（4）若a=2，b=3，则c=____。"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text=source,
                stem=[{"kind": "text", "value": source}],
            )
            analysis = json.loads(path.read_text(encoding="utf-8"))
            page = analysis["pages"][0]
            base_question = page["questions"][0]
            split_at = source.index("（2）")
            first_text = source[:split_at]
            second_text = source[split_at:]
            base_question["sourceText"] = first_text
            base_question["sourceEvidence"] = [
                {"shapeId": "6", "start": 0, "end": split_at}
            ]
            base_question["stem"] = [{"kind": "text", "value": first_text}]
            second_question = copy.deepcopy(base_question)
            second_question["ref"] = "p0001-i01"
            second_question["itemIndex"] = 1
            second_question["sourceText"] = second_text
            second_question["sourceEvidence"] = [
                {"shapeId": "6", "start": split_at, "end": len(source)}
            ]
            second_question["stem"] = [{"kind": "text", "value": second_text}]
            page["questions"] = [base_question, second_question]
            path.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")

            ir, report = build_question_ir.build(path)

        self.assertIsNone(ir)
        self.assertFalse(report["ok"])
        self.assertTrue(any("默认只能生成一道题" in error for error in report["errors"]))
        self.assertTrue(any("以小问标记开头" in error for error in report["errors"]))

    def test_numbered_subquestions_stay_in_one_page_level_question(self) -> None:
        source = (
            "在△ABC中，∠C=90°，AB=c，BC=a，AC=b。\n"
            "（1）若a=3，b=4，则c=____；\n"
            "（2）若a=24，c=25，则b=____；\n"
            "（3）若a=1.2，b=1.6，则c=____；\n"
            "（4）若a=2，b=3，则c=____。"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text=source,
                stem=[{"kind": "text", "value": source}],
            )

            ir, report = build_question_ir.build(path)

        self.assertTrue(report["ok"], report["errors"])
        self.assertIsNotNone(ir)
        self.assertEqual(report["questionPageCount"], 1)
        self.assertEqual(report["questionCount"], 1)
        self.assertIn("（4）", ir["questions"][0]["stem"][0]["value"])

    def test_independent_top_level_questions_can_split_with_explicit_evidence(self) -> None:
        first_text = "例1：计算直角三角形斜边长。"
        second_text = "例2：证明勾股定理。"
        source = f"{first_text}\n{second_text}"
        with tempfile.TemporaryDirectory() as directory:
            path = self._analysis(
                Path(directory),
                source_text=source,
                native_text=source,
                stem=[{"kind": "text", "value": source}],
            )
            analysis = json.loads(path.read_text(encoding="utf-8"))
            page = analysis["pages"][0]
            base_question = page["questions"][0]
            base_question["sourceText"] = first_text
            base_question["sourceEvidence"] = [
                {"shapeId": "6", "start": 0, "end": len(first_text)}
            ]
            base_question["stem"] = [{"kind": "text", "value": first_text}]
            second_question = copy.deepcopy(base_question)
            second_question["ref"] = "p0001-i01"
            second_question["itemIndex"] = 1
            second_question["sourceText"] = second_text
            second_question["sourceEvidence"] = [
                {
                    "shapeId": "6",
                    "start": len(first_text) + 1,
                    "end": len(source),
                }
            ]
            second_question["stem"] = [{"kind": "text", "value": second_text}]
            page["questions"] = [base_question, second_question]
            page["questionGrouping"] = {
                "mode": "multiple",
                "reason": "页面明确包含两个互不依赖的例题区块",
                "independentTopLevelLabels": ["例1", "例2"],
            }
            path.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")

            ir, report = build_question_ir.build(path)

        self.assertTrue(report["ok"], report["errors"])
        self.assertIsNotNone(ir)
        self.assertEqual(report["questionCount"], 2)
        self.assertEqual(report["multiQuestionPageCount"], 1)
        self.assertEqual(
            report["questionGrouping"]["policy"],
            "one-question-per-page-default",
        )


if __name__ == "__main__":
    unittest.main()
