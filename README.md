# qulifang-ppt-tex

[![Validate Skill](https://github.com/MAX400296/qulifang-ppt-tex/actions/workflows/validate.yml/badge.svg)](https://github.com/MAX400296/qulifang-ppt-tex/actions/workflows/validate.yml)

`qulifang-ppt-tex` 是一个面向 Codex 的公开 Skill，用于把 PowerPoint 课件转换为可审查、可验证、可导入的结构化课件包。

它会同时使用两条证据链：

- 将每一页渲染为高清图片，作为版式、公式和配图的视觉真相源；
- 解析 PPTX/OOXML 中的原生文字、形状和媒体，用于校正文字、公式及配图边界。

输出包括讲解页/题目页分类、逐字题干、KaTeX 兼容公式、白底高清配图、验证报告，以及 EducationApp 管理后台“代码上传”入口可直接导入的 ZIP。

## 当前版本

`v1.0.0`

首个公开稳定版锁定以下规则：一页题目默认只生成一道题，多问保留为同一道题的子问；完整保留同页多张语义配图；配图使用白底和高清门槛；识别、题目边界与配图均经过阻断式校验。

## 安装

该 Skill 依赖公开的 [`qulifang-to-tex`](https://github.com/MAX400296/qulifang-to-tex)。请让 Codex 使用 `$skill-installer` 依次安装下面两个地址：

```text
https://github.com/MAX400296/qulifang-to-tex/tree/main/skills/qulifang-to-tex
https://github.com/MAX400296/qulifang-ppt-tex/tree/main/skills/qulifang-ppt-tex
```

也可以直接运行 Codex 自带安装器：

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo MAX400296/qulifang-to-tex \
  --path skills/qulifang-to-tex

python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo MAX400296/qulifang-ppt-tex \
  --path skills/qulifang-ppt-tex
```

安装后，在新一轮 Codex 对话中使用：

```text
$qulifang-ppt-tex 请处理这个 PPTX
```

## 同步更新

向 Codex 输入：

```text
更新 qulifang-ppt-tex
```

Skill 会下载 `main` 分支的公开版本，校验版本号与目录结构，备份现有安装后再替换。也可以直接运行：

```bash
python3 ~/.codex/skills/qulifang-ppt-tex/scripts/update_skill.py
```

如需固定版本，首次安装时使用 `--ref v1.0.0`。公开版本采用语义化版本号和 GitHub Release 发布。

## 运行要求

- Python 3.10+
- Pillow
- Microsoft PowerPoint（优先）或 LibreOffice
- Poppler 的 `pdftoppm`
- 已安装的 `qulifang-to-tex` Skill

Skill 不会把 PPT 内容自动上传到本仓库。除非用户显式选择云端 OCR，课件解析和配图处理均在本机完成。

## 仓库结构

```text
skills/qulifang-ppt-tex/
├── SKILL.md
├── agents/openai.yaml
├── distribution.json
├── references/
└── scripts/
```

## License

[MIT](LICENSE)
