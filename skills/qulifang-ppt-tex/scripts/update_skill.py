#!/usr/bin/env python3
"""从 GitHub 安全更新已安装的 qulifang-ppt-tex skill。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path


SKILL_NAME = "qulifang-ppt-tex"
CONFIG_NAME = "distribution.json"


class UpdateError(Exception):
    """更新前置条件、下载或校验失败。"""


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateError(f"无法读取版本配置：{path}") from exc
    if not isinstance(value, dict):
        raise UpdateError(f"版本配置不是对象：{path}")
    return value


def parse_github_source(
    url: str,
    default_path: str,
    default_ref: str,
) -> tuple[str, str, str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc != "github.com":
        raise UpdateError("更新源必须是 github.com 链接")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise UpdateError("GitHub 更新源链接格式不正确")
    owner, repo = parts[0], parts[1]
    ref = default_ref
    path = default_path
    if len(parts) > 2:
        if parts[2] == "tree":
            if len(parts) < 4:
                raise UpdateError("GitHub tree 链接缺少版本或路径")
            ref = parts[3]
            path = "/".join(parts[4:]) or default_path
        else:
            path = "/".join(parts[2:])
    return owner, repo, ref, path.strip("/")


def download_repository(owner: str, repo: str, ref: str, target: Path) -> Path:
    archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/{ref}"
    request = urllib.request.Request(
        archive_url,
        headers={"User-Agent": "qulifang-ppt-tex-updater"},
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    archive_path = target / "repository.zip"
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            archive_path.write_bytes(response.read())
    except OSError as exc:
        raise UpdateError(f"下载 GitHub 更新源失败：{exc}") from exc

    extract_root = target / "repository"
    extract_root.mkdir()
    try:
        with zipfile.ZipFile(archive_path) as archive:
            root = extract_root.resolve()
            for info in archive.infolist():
                destination = (extract_root / info.filename).resolve()
                if destination != root and not str(destination).startswith(f"{root}{os.sep}"):
                    raise UpdateError("更新包包含越界文件路径")
            archive.extractall(extract_root)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateError("GitHub 更新包无法解压") from exc

    top_levels = [item for item in extract_root.iterdir() if item.is_dir()]
    if len(top_levels) != 1:
        raise UpdateError("GitHub 更新包目录结构异常")
    return top_levels[0]


def validate_candidate(candidate: Path) -> dict[str, object]:
    if not (candidate / "SKILL.md").is_file():
        raise UpdateError("更新包缺少 SKILL.md")
    version_path = candidate / CONFIG_NAME
    version_info = read_json(version_path) if version_path.is_file() else {}

    quick_validate = codex_home() / "skills/.system/skill-creator/scripts/quick_validate.py"
    if quick_validate.is_file():
        result = subprocess.run(
            ["python3", str(quick_validate), str(candidate)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stdout + result.stderr).strip()
            raise UpdateError(f"新版本 skill 校验失败：{detail}")
    return version_info


def version_text(value: object) -> str:
    return str(value or "unknown").strip() or "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="更新 qulifang-ppt-tex skill")
    parser.add_argument("--url", help="GitHub skill 目录链接；省略时读取 distribution.json")
    parser.add_argument("--ref", help="覆盖更新分支或 tag")
    parser.add_argument("--dest", type=Path, help="技能安装根目录，默认 ~/.codex/skills")
    parser.add_argument("--force", action="store_true", help="版本号相同也强制刷新")
    parser.add_argument("--dry-run", action="store_true", help="只下载和校验，不替换本地版本")
    args = parser.parse_args()

    destination_root = (args.dest or (codex_home() / "skills")).expanduser().resolve()
    current_dir = destination_root / SKILL_NAME
    local_config_path = current_dir / CONFIG_NAME
    local_config = read_json(local_config_path) if local_config_path.is_file() else {}
    repository = str(args.url or local_config.get("repository") or "").strip()
    if not repository:
        raise UpdateError(
            "未配置 GitHub 更新源；请使用 --url，或在 distribution.json 中填写 repository"
        )

    default_path = str(local_config.get("path") or f"skills/{SKILL_NAME}")
    default_ref = str(args.ref or local_config.get("ref") or "main")
    owner, repo, ref, skill_path = parse_github_source(repository, default_path, default_ref)
    if args.ref:
        ref = args.ref

    with tempfile.TemporaryDirectory(prefix="qulifang-ppt-tex-update-") as temp_dir:
        repository_root = download_repository(owner, repo, ref, Path(temp_dir))
        candidate = (repository_root / skill_path).resolve()
        repository_real_root = repository_root.resolve()
        if candidate != repository_real_root and not str(candidate).startswith(
            f"{repository_real_root}{os.sep}"
        ):
            raise UpdateError("skill 路径越出 GitHub 仓库")
        if not candidate.is_dir():
            raise UpdateError(f"GitHub 仓库中不存在 skill 目录：{skill_path}")
        latest_config = validate_candidate(candidate)
        old_version = version_text(local_config.get("version"))
        new_version = version_text(latest_config.get("version"))
        print(f"当前版本：{old_version}；远程版本：{new_version}；来源：{owner}/{repo}@{ref}")
        if old_version == new_version and not args.force:
            print("版本号相同，无需更新；如需重新覆盖请加 --force。")
            return 0
        if args.dry_run:
            print("dry-run：校验通过，未替换本地文件。")
            return 0

        destination_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_dir = destination_root / f"{SKILL_NAME}.backup-{timestamp}"
        staging_dir = destination_root / f".{SKILL_NAME}.update-{os.getpid()}"
        old_dir = destination_root / f".{SKILL_NAME}.old-{os.getpid()}"
        try:
            shutil.copytree(candidate, staging_dir)
            if current_dir.exists():
                shutil.copytree(current_dir, backup_dir)
                current_dir.rename(old_dir)
            staging_dir.rename(current_dir)
            if old_dir.exists():
                shutil.rmtree(old_dir)
        except (OSError, shutil.Error) as exc:
            if current_dir.exists() and current_dir.is_dir():
                shutil.rmtree(current_dir, ignore_errors=True)
            if old_dir.exists():
                old_dir.rename(current_dir)
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            raise UpdateError(f"替换本地 skill 失败，旧版本已保留：{exc}") from exc

        print(f"已更新：{current_dir}")
        print(f"备份版本：{backup_dir}")
        print(f"版本：{old_version} → {new_version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as exc:
        raise SystemExit(f"更新失败：{exc}")
