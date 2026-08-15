from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(SCRIPTS_DIR))

import preflight_check  # noqa: E402


class PreflightCheckTest(unittest.TestCase):
    def _skill_with_dependency(self, root: Path) -> Path:
        skill = root / "skills" / "qulifang-ppt-tex"
        dependency_scripts = root / "skills" / "qulifang-to-tex" / "scripts"
        skill.mkdir(parents=True)
        dependency_scripts.mkdir(parents=True)
        (dependency_scripts / "build_package.py").write_text("", encoding="utf-8")
        (dependency_scripts / "validate_package.py").write_text("", encoding="utf-8")
        return skill

    def test_auto_prefers_powerpoint_when_all_required_dependencies_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = self._skill_with_dependency(root)
            powerpoint = root / "Microsoft PowerPoint.app"
            powerpoint.mkdir()
            commands = {
                "osascript": "/usr/bin/osascript",
                "pdftoppm": "/usr/local/bin/pdftoppm",
            }
            report = preflight_check.check_environment(
                renderer="auto",
                skill_dir=skill,
                which=commands.get,
                platform_name="darwin",
                python_version=(3, 12, 0),
                pillow_available=True,
                powerpoint_app=powerpoint,
                static_soffice_candidates=[],
                home_dir=root,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["selectedRenderer"], "microsoft-powerpoint")
        self.assertEqual(report["blockingMissing"], [])

    def test_auto_uses_libreoffice_when_powerpoint_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = self._skill_with_dependency(root)
            commands = {
                "soffice": "/usr/local/bin/soffice",
                "pdftoppm": "/usr/local/bin/pdftoppm",
            }
            report = preflight_check.check_environment(
                renderer="auto",
                skill_dir=skill,
                which=commands.get,
                platform_name="darwin",
                python_version=(3, 12, 0),
                pillow_available=True,
                powerpoint_app=root / "missing.app",
                static_soffice_candidates=[],
                home_dir=root,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["selectedRenderer"], "libreoffice")

    def test_missing_required_dependencies_block_execution_and_offer_installation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "qulifang-ppt-tex"
            skill.mkdir(parents=True)
            report = preflight_check.check_environment(
                renderer="auto",
                skill_dir=skill,
                which=lambda _: None,
                platform_name="darwin",
                python_version=(3, 9, 0),
                pillow_available=False,
                powerpoint_app=root / "missing.app",
                static_soffice_candidates=[],
                home_dir=root,
                codex_home=root / "empty-codex",
            )
            human = preflight_check.format_human_report(report, "python3 preflight_check.py")

        self.assertFalse(report["ok"])
        self.assertEqual(
            set(report["blockingMissing"]),
            {"python", "pillow", "qulifang-to-tex", "renderer", "pdftoppm"},
        )
        self.assertIn("已阻止 PPT 处理", human)
        self.assertIn("brew install poppler", human)
        self.assertIn("安装完成后重新运行", human)

    def test_external_pages_do_not_require_pdf_renderer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = self._skill_with_dependency(root)
            pages = root / "pages"
            pages.mkdir()
            (pages / "page-0001.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            report = preflight_check.check_environment(
                renderer="external",
                external_pages=pages,
                skill_dir=skill,
                which=lambda _: None,
                platform_name="linux",
                python_version=(3, 12, 0),
                pillow_available=True,
                home_dir=root,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["selectedRenderer"], "external-pages")
        self.assertNotIn("pdftoppm", [item["id"] for item in report["checks"]])


if __name__ == "__main__":
    unittest.main()
