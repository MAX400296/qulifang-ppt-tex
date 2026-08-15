---
name: qulifang-ppt-tex
description: Convert attached PowerPoint PPTX courseware into verifiable slide images, lecture/question page classifications, structured math questions, KaTeX-compatible LaTeX, preserved figures, and an EducationApp-compatible TEX ZIP. Use when the user asks to read, classify, transcribe, extract, digitize, or import a PPT/PPTX deck; separate lecture pages from question pages; keep multi-part prompts together as one page-level question by default; preserve formulas or diagrams; or create a package for the 趣立方管理后台.
---

# Qulifang PPT TEX

Convert a PPTX in two synchronized lanes: render every slide for visual truth, and parse OOXML for native text/media evidence. Fuse both lanes into a reviewed page manifest, then reuse `qulifang-to-tex` to build the existing `educationapp-question-tex/v1` import package.

## Update mode

If the user says “更新 qulifang-ppt-tex” or asks to update this skill, do not process an attached presentation. Run the bundled updater instead:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/update_skill.py"

The updater reads `distribution.json`, downloads the configured public GitHub version, validates the candidate skill, compares explicit version numbers, backs up the current installation, and replaces it only after validation succeeds. Report the old version, new version, source ref, backup path, or the reason no update was performed.

## Zero-parameter attachment mode

When the user attaches one or more PPT/PPTX files and invokes this skill without further instructions, start immediately.

Apply these defaults:

- Process every slide in source order.
- Classify every slide as `lecture` or `question`.
- Generate exactly one question for each question slide by default. Keep `（1）（2）（3）` and similar numbered parts inside that question as ordered subquestions.
- Convert readable formulas to KaTeX-compatible LaTeX.
- Preserve geometry diagrams, graphs, charts, complex tables, and image-only options as white-background semantic image assets.
- Render from a PowerPoint-native PDF at 480 DPI when available. Require at least 360 effective DPI for every source page and reject low-resolution figure crops instead of interpolating them.
- Extract only source content. Leave missing answers and analyses empty unless the user explicitly requests solving.
- Generate a page review manifest plus an EducationApp-compatible question ZIP.
- Use the canonical sibling review directory `SOURCE-STEM-qulifang-ppt-tex`; refresh only a directory already marked with `protocol: qulifang-ppt-tex`.
- Save the final importable ZIP beside the source PPTX as `SOURCE-STEM-ppt-code-import.zip` unless the user explicitly requests another location.
- Treat uncertainty as `needsReview`; do not block on ordinary recognition uncertainty.

Ask only when no readable PowerPoint file is available, the file is encrypted/corrupt, or safe output creation is impossible.

## Non-negotiable rules

- Treat the rendered slide image as the visual source of truth.
- Use native OOXML text to correct OCR/transcription, not to replace visual inspection.
- Transcribe question wording verbatim. Never summarize, polish, shorten, or silently rewrite source prose, even when it is repetitive.
- Record the complete question-region transcription in `question.sourceText` before structuring it. Use `sourceTextOrigin: native` when it is traceable to OOXML text, otherwise use `visual` after checking the rendered page.
- Never use Pandoc, MarkItDown, or OCR alone as the source of truth.
- Never infer visual reading order only from XML object order.
- Do not flatten diagrams assembled from PowerPoint shapes into disconnected labels. Preserve them from the rendered slide.
- Inventory every visible semantic figure on a question slide before choosing assets. When the same question region contains multiple diagrams, graphs, tables, or numbered figures, retain all of them by default; an explicit reference such as `图2` is not evidence that `图1` is disposable when it supplies comparison, prior-method, or contextual information.
- Never use a hand-estimated pixel rectangle as the primary figure boundary. Select one or more OOXML Shape candidates, or use an explicitly reviewed PowerPoint selection/raster fallback.
- Export every question figure on a white background. Re-map light neutral strokes/text to dark ink when the original slide uses a dark theme; preserve semantic colors.
- Treat high definition as a measurable requirement: source pages must be at least 360 effective DPI, and normal figure crops must have a short edge of at least 600 px and a long edge of at least 800 px.
- Never upscale a low-resolution crop to satisfy pixel dimensions. Re-render the native PowerPoint/PDF at 480–600 DPI so lines, labels, and formulas gain real source pixels.
- Reject rather than package a figure that is clipped, includes question prose/title/footer/page number, lacks labels or units, touches its crop boundary, or has not been visually compared with the annotated source page.
- Keep formulas as text/LaTeX when legible; use an image only when faithful transcription is uncertain.
- Keep question type editable. Normalize selection questions to `选择题`, fill/judgment questions to `填空题`, and calculation/proof/application questions to `应用题`.
- Preserve source wording, option order, subquestion order, answers, and analyses.
- Treat one question slide as one database question by default. Never turn `（1）（2）（3）`, several blanks, or several calculations under a shared premise into separate questions.
- Allow multiple questions on one slide only when the page visibly contains independent top-level question blocks with no shared premise, diagram, or source-text range; record `questionGrouping.mode=multiple`, the reason, and every independent top-level label.
- Never invent missing content unless the user explicitly asks to solve or enrich the questions.
- Keep every slide in `ppt-analysis.json`, including lecture pages and uncertain pages.
- Build the final courseware ZIP with the original PPTX, complete page classification, question-to-page mapping, and the validated TEX question package. This is the artifact accepted by the EducationApp “代码上传” courseware entry.

## Workflow

### 0. Run the mandatory dependency preflight

Before opening the presentation, creating an output directory, or running any extraction script, run:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/preflight_check.py" --renderer auto

Treat exit code `2` or `ok=false` as a hard stop. Show the user the complete missing-component report and the proposed installation commands. Dependency installation changes the machine, so obtain the user's approval before running those commands. After installation, run the same preflight again. Do not start any later workflow step until it exits with code `0` and reports `ok=true`.

`auto` requires Python 3.10+, Pillow, `qulifang-to-tex`, `pdftoppm`, and at least one usable renderer. It prefers Microsoft PowerPoint on macOS and otherwise uses LibreOffice. Missing optional XeLaTeX preview support does not block the importable ZIP. If verified high-resolution slide images already exist, use `--renderer external --external-pages "/absolute/pages"`; this is the only mode that does not require `pdftoppm` or a local office renderer.

Never hide a missing dependency, continue with a partial toolchain, or begin PPT recognition while installation is pending.

### 1. Resolve tools and output

Resolve this skill directory as `QULIFANG_PPT_SKILL_DIR`.

Resolve the sibling `qulifang-to-tex` skill as `QULIFANG_TEX_SKILL_DIR`. Prefer the sibling directory next to this skill; otherwise locate it under the active Codex skills directory. Stop with a clear dependency error if its builder and validator are unavailable.

Create a new empty work directory. Do not overwrite unrelated output. Read [analysis-schema.md](references/analysis-schema.md) before authoring `ppt-analysis.json`.

For a legacy `.ppt` source, first convert a copy to `.pptx` with Microsoft PowerPoint or LibreOffice and keep that copy beside the original `.ppt`. Never overwrite the original or an unrelated existing `.pptx`. Record the conversion tool in warnings, then use the converted `.pptx` for every following step so the default final ZIP still lands in the original folder.

### 2. Prepare structure and render every slide

Prefer Microsoft PowerPoint's native PDF export when PowerPoint is available, because it preserves PowerPoint fonts, grouped shapes, equations, and vector geometry more faithfully than LibreOffice. Export a temporary PDF without changing the source PPTX, then let `prepare_pptx.py` rasterize it at 480 DPI:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/export_powerpoint_pdf.py" \
      --input "/absolute/source.pptx" \
      --output "/absolute/powerpoint-native.pdf" \
      --replace-existing

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/prepare_pptx.py" \
      --input "/absolute/source.pptx" \
      --output "/absolute/work" \
      --renderer none \
      --rendered-pdf "/absolute/powerpoint-native.pdf" \
      --dpi 480 \
      --min-effective-dpi 360 \
      --replace-existing

If PowerPoint is unavailable, use the available Codex presentation renderer only when it can export at least 360 effective DPI. Do not hard-code a versioned internal renderer path. Register its rendered pages while extracting OOXML evidence:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/prepare_pptx.py" \
      --input "/absolute/source.pptx" \
      --output "/absolute/work" \
      --renderer none \
      --pages-dir "/absolute/rendered-pages" \
      --min-effective-dpi 360

If the Codex presentation renderer is unavailable, use the open-source fallback directly:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/prepare_pptx.py" \
      --input "/absolute/source.pptx" \
      --output "/absolute/work" \
      --renderer libreoffice \
      --dpi 480 \
      --min-effective-dpi 360 \
      --replace-existing

Read `work/source-info.json` and verify that the rendered page count equals the PPTX slide count and `renderMetrics.minimumEffectiveDpi >= 360`. If rendering changes fonts or clips content, record a warning and switch to a PowerPoint-native export.

Generate deterministic per-text-box evidence before recognizing any question:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/build_recognition_evidence.py" \
      --work "/absolute/work" \
      --output "/absolute/work/recognition-evidence.json"

Use this file instead of copying from flattened `nativeText`. It excludes known headers, footers, page numbers, and question-label titles while preserving exact text-box content and character offsets.

### 3. Inspect every slide

Inspect all images in `work/pages/`, in bounded batches for long decks. Use `work/slides/slide-NNNN.json` only as evidence for exact text and embedded media.

For each slide:

1. Decide `lecture` versus `question` from the complete page, not from keywords alone.
2. Create one `itemIndex: 0` question for the page by default. Preserve every visible subquestion marker and all parts in the same `sourceText` and `stem`.
3. Split a page only when it contains multiple independent top-level question blocks. Require disjoint source evidence plus `questionGrouping.mode=multiple`, a reason, and `independentTopLevelLabels`; mark uncertain boundaries as blocking instead of splitting.
4. First pass: select exact character ranges from `recognition-evidence.json`. Copy every sentence, premise, parenthetical note, option, and learner instruction into `sourceText`; record every range in `sourceEvidence`.
5. Second pass: independently compare `sourceText`, formulas, option order, subquestion order, and question boundaries against the rendered page. Record the checks in `textReview` with `reviewPasses >= 2`.
6. Derive `stem` and `options` without paraphrasing. For image-only or incomplete-OOXML pages use `sourceTextOrigin: visual`, transcribe from the rendered page, and still complete the two-pass review.
7. Transcribe formulas into balanced KaTeX-compatible LaTeX. Keep inline formulas inside the surrounding sentence; never represent `文字 → 行内公式 → 文字` as separate paragraphs.
8. Inventory every semantic figure in the complete question region before assigning assets. Retain all figures by default, including preceding/reference figures needed to understand phrases such as `参照上述证法`; exclude only demonstrably decorative or unrelated candidates and record each exclusion with a concrete visual reason. Preserve labels, axes, legends, units, line endpoints, and arrowheads.
9. For `图1/图2`, before/after, comparison, construction-step, or jointly referenced figures, prefer one combined white-background asset rendered from all related candidates in source reading order. Use separate ordered assets only when the figures are visually independent. Never discard one figure merely because the final instruction names another.
10. Keep a full-page image reference for review, but do not insert the whole page into the question unless no reliable semantic crop is possible.
11. Record ambiguous boundaries, formulas, image ownership, answer ownership, or page type in `needsReview`.

Use the image-viewing tool for visual inspection. PaddleOCR may be used only as a fallback for scanned/image-only content; verify its output against the rendered slide.

### 4. Extract and render figures on white

When any question needs a figure, generate Shape-based candidates for every slide:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/extract_figure_candidates.py" \
      --work "/absolute/work" \
      --output "/absolute/work/figure-candidates.json" \
      --annotated-dir "/absolute/work/figure-candidates"

Inspect the annotated source page and assign every visible semantic figure in the question region. Select the smallest candidate set that contains each complete figure, but do not minimize away companion figures or contextual diagrams. A multi-part, numbered, comparison, or derivation figure normally requires multiple `--candidate` values in one combined asset. Do not select a candidate containing question prose, slide title, footer, page number, or unrelated figures.

Before rendering, compare all diagram-like candidates on each question page with the selected candidate IDs. Every diagram-like candidate must be retained by an asset or listed in top-level `figureCandidateExclusions` with a specific visually verified reason. Use exclusions only for false-positive candidate groups, decoration, logos, or content demonstrably outside the question; `题干只提到图2` and similar explicit-reference-only reasons are invalid.

Candidate generation automatically attaches nearby short labels, values, variables, and units while excluding question-label titles and long prose. Inspect `autoIncludedLabelShapeIds`, `includedTextLabels`, and `shapeRecords`; change the candidate instead of accepting an unrelated auto-label.

Render every selected candidate set as a white-background PNG:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/render_figure_white.py" \
      --candidates "/absolute/work/figure-candidates.json" \
      --page 11 \
      --candidate "p0011-c001" \
      --candidate "p0011-c002" \
      --asset-id "img_p0011_i00_stem_01" \
      --output "/absolute/work/crops/p0011-i00-stem-01.png" \
      --metadata-output "/absolute/work/crops/p0011-i00-stem-01.figure.json" \
      --review-output "/absolute/work/crop-review/p0011-i00-stem-01-source.png" \
      --padding 0.04

The renderer enforces the high-definition profile by default: short edge >= 600 px and long edge >= 800 px. If it rejects a crop, re-render the PowerPoint-native PDF at 480 DPI or 600 DPI and regenerate candidates; do not resize the rejected crop.

Prefer `shape-candidates`. If PowerPoint groups a figure in a way that candidates cannot isolate reliably, export an explicitly selected PowerPoint Shape group on a white temporary slide and use `cropMethod: powerpoint-selection`. Use `raster-semantic` only when the source figure is already a bitmap. All methods still require 2–8% white padding and visual comparison against the full slide.

Inspect both the source overlay and the white crop for every asset. Set `figureQuality.visualReview.status` to `passed` and `reviewedAgainstPage` to `true` only after confirming that nothing is missing and nothing unrelated is present. Copy candidate IDs, Shape IDs, padding, background, and crop method from the generated `.figure.json` into the asset record.

Also set `figureQuality.cropMetadata` to that generated `.figure.json`, copy `includedTextLabels`, and complete `semanticReview` (`labelsVerified`, `lineEndpointsVerified`, `unrelatedContentExcluded`, `localCropOpened`). Never type Shape IDs or label lists from memory. Require `renderCoverageRatio >= 0.995`.

### 5. Author and validate the page manifest

Create `work/ppt-analysis.json` using [analysis-schema.md](references/analysis-schema.md). Include every source slide exactly once.

Set `recognitionEvidence` to `recognition-evidence.json`. When assets exist, set `figureCandidates` to `figure-candidates.json` and `figureValidationReport` to `figure-validation-report.json`. Generate a preliminary contact sheet even while visual reviews are pending:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/validate_figure_assets.py" \
      --analysis "/absolute/work/ppt-analysis.json" \
      --report "/absolute/work/figure-validation-report.json" \
      --contact-sheet "/absolute/work/figure-contact-sheet.jpg" \
      --allow-pending-review

Inspect the contact sheet and every source overlay, record the completed visual reviews in `ppt-analysis.json`, then run the same command again without `--allow-pending-review`. Do not continue unless it reports `ok=true`. The final report is hash-bound to the exact figure files, so any later figure edit requires revalidation.

Then generate the normalized question IR:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/build_question_ir.py" \
      --analysis "/absolute/work/ppt-analysis.json" \
      --output "/absolute/work/questions.ir.json" \
      --report "/absolute/work/ppt-validation-report.json"

Do not continue if the command reports errors. Fix the manifest rather than editing generated IR by hand.

The report must show `sourceFidelity.checkedQuestionCount == questionCount`. Every question must meet the source-fidelity threshold and, for `sourceTextOrigin: native`, the native-text trace threshold. A low score is a blocking transcription error, not an ordinary warning.

### 6. Build the EducationApp courseware package

When the analysis contains question items, first run the existing deterministic question-package builder:

    python3 "$QULIFANG_TEX_SKILL_DIR/scripts/build_package.py" \
      --ir "/absolute/work/questions.ir.json" \
      --output "/absolute/output/question-package" \
      --replace-existing

Add `--compile-preview` only when XeLaTeX is available.

Always validate the generated package:

    python3 "$QULIFANG_TEX_SKILL_DIR/scripts/validate_package.py" \
      "/absolute/output/question-package"

Inspect a representative mixed formula sentence in the generated `questions.tex`. Inline math must remain within its sentence, for example `以 $a,b$ 为直角边、以 $c$ 为斜边`, with no blank lines around `$a,b$` or `$c$`.

Then create the single ZIP accepted by the web admin “代码上传” entry. Pass the exact ZIP produced inside `question-package/`. Omit `--output` to use the default location beside the source PPTX:

    python3 "$QULIFANG_PPT_SKILL_DIR/scripts/build_courseware_package.py" \
      --analysis "/absolute/work/ppt-analysis.json" \
      --ir "/absolute/work/questions.ir.json" \
      --validation-report "/absolute/work/ppt-validation-report.json" \
      --source-pptx "/absolute/source.pptx" \
      --question-package "/absolute/output/question-package/<title>-code-import.zip"

The default final path is `/absolute/source-ppt-code-import.zip`. Use `--output "/another/path/name.zip"` only when the user requests a different location.

If `questionCount` is zero, skip the `qulifang-to-tex` builder and omit `--question-package`; the final ZIP will import as a pure lecture courseware.

Keep these review artifacts next to the final ZIP for manual inspection:

- `ppt-analysis.json`
- `recognition-evidence.json`
- `ppt-validation-report.json`
- `figure-validation-report.json` and `figure-contact-sheet.jpg` when figures exist;
- `figure-candidates.json`, `figure-candidates/`, `crop-review/`, and `crops/` when figures exist;
- `source-info.json`
- `pages/`
- `slides/`

The importable courseware ZIP is the file produced by `build_courseware_package.py` beside the source PPTX by default. The inner `educationapp-question-tex/v1` ZIP remains independently valid and is embedded unchanged.

### 7. Final quality gate

Require all of the following:

- Rendered page count equals source slide count.
- `ppt-analysis.json` contains every page number exactly once.
- Every `question` page contains at least one question; unresolved boundaries are blocking review items and prevent packaging.
- Every `question` page contains exactly one question by default; numbered subquestions remain together in that question's stem.
- Every multi-question-page exception has disjoint evidence and explicit `questionGrouping` proof; repeated premises or subquestion-prefixed items are blocking errors.
- Every `lecture` page contains no question item.
- Question and option order match the slide.
- Every question has verbatim `sourceText`, and the generated stem/options pass the source-fidelity gate; summaries and polished rewrites are blocking errors.
- Every native-text question resolves exact `sourceEvidence` ranges; every question has a completed two-pass `textReview`.
- Inline formulas remain inline with surrounding prose in both `questions.ir.json` and `questions.tex`.
- Every visible formula is either faithful LaTeX or explicitly marked for review.
- Every retained figure maps to exactly one unique asset ID and file.
- Every diagram-like candidate on a question page is either retained by an asset or has a specific, visually reviewed `figureCandidateExclusions` record; mentioning only one numbered figure never justifies dropping its companion figure.
- Every numbered, comparison, before/after, or derivation figure set preserves all members and their source order, preferably in one combined white-background asset.
- Every Shape-based figure matches its crop metadata, includes candidate-attached labels, and has at least 99.5% source-to-output foreground coverage.
- Every retained figure uses a white background, has 2–8% padding, and is traceable to Shape candidates or an explicitly reviewed fallback.
- Every rendered source page has at least 360 effective DPI; every normal retained figure has a short edge of at least 600 px and a long edge of at least 800 px.
- Every figure validation report contains the high-definition quality profile and explicitly rejects interpolation upscaling.
- No retained figure touches the crop edge or contains question prose, title, decorative logo, footer, page number, slide background, or unrelated nearby content.
- Every retained figure has been compared with both its annotated source overlay and the white crop; all labels, axes, legends, units, line endpoints, and arrowheads remain present.
- `validate_figure_assets.py` reports `ok=true`, covers every figure ID, and its recorded hashes match the current files.
- `build_question_ir.py` reports `ok=true`.
- `validate_package.py` reports success.
- `build_courseware_package.py` reports `ok=true` and its page/question counts match the validation report.

Local figure validation proves crop content, padding, and package integrity only. It does not prove that a deployed web admin can fetch a private image URL. If the review page shows a gray `题干图1` placeholder while the local crop is correct, diagnose the storage/authenticated-image request separately and do not reclassify it as a crop failure.

Do not report completion while a blocking error remains.

## Output contract

Return links to:

- the output directory;
- `ppt-analysis.json`;
- `recognition-evidence.json`;
- `figure-candidates.json`, `figure-contact-sheet.jpg`, and `figure-validation-report.json` when figures exist;
- `questions.ir.json`;
- the generated `questions.tex`;
- the final `*-ppt-code-import.zip` accepted by the courseware “代码上传” entry;
- the inner TEX question ZIP when the deck contains questions;
- preview PDF when generated.

Report slide count, lecture-page count, question-page count, extracted question count, figure count, source-fidelity checked count/minimum score, warnings, and `needsReview` count. State which renderer was used and whether PaddleOCR or answer generation was used.

Unless the user requested another location, confirm that the final ZIP was saved in the same folder as the source PPTX.

## Reference routing

- Read [analysis-schema.md](references/analysis-schema.md) before creating or modifying `ppt-analysis.json`.
- Read [package-schema.md](references/package-schema.md) when debugging the final courseware ZIP or the web-admin import contract.
- Read the sibling `qulifang-to-tex/references/ir-schema.md` only when debugging generated question IR.
- Read the sibling `qulifang-to-tex/references/protocol-v1.md` only when debugging `questions.tex` or image IDs.
