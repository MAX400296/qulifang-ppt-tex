# PPT analysis schema

Create one UTF-8 JSON object named `ppt-analysis.json`.

```json
{
  "protocol": "qulifang-ppt-tex",
  "version": 1,
  "title": "勾股定理",
  "source": {
    "filename": "勾股定理.pptx",
    "sha256": "optional-sha256",
    "pageCount": 12
  },
  "pages": [
    {
      "pageNo": 1,
      "pageType": "lecture",
      "confidence": 0.99,
      "pageImage": "pages/page-0001.png",
      "nativeText": "勾股定理",
      "questions": []
    },
    {
      "pageNo": 8,
      "pageType": "question",
      "confidence": 0.96,
      "pageImage": "pages/page-0008.png",
      "nativeText": "证明勾股定理……",
      "questions": [
        {
          "ref": "p0008-i00",
          "itemIndex": 0,
          "sourceNumber": "1",
          "sourceText": "以 a、b 为直角边，以 c 为斜边作两个全等的直角三角形，并按图拼成梯形。利用该图证明勾股定理，写出证明过程。",
          "sourceTextOrigin": "native",
          "sourceEvidence": [
            {"shapeId": "6", "start": 0, "end": 56}
          ],
          "textReview": {
            "status": "passed",
            "reviewedAgainstPage": true,
            "formulaChecked": true,
            "optionOrderChecked": true,
            "reviewPasses": 2
          },
          "type": "应用题",
          "metadata": {},
          "stem": [
            {"kind": "text", "value": "以 "},
            {"kind": "latex", "value": "$a,b$"},
            {"kind": "text", "value": " 为直角边，以 "},
            {"kind": "latex", "value": "$c$"},
            {"kind": "text", "value": " 为斜边作两个全等的直角三角形，并按图拼成梯形。利用该图证明勾股定理，写出证明过程。"},
            {"kind": "image", "assetId": "img_p0008_i00_stem_01"}
          ],
          "options": {},
          "answer": [],
          "analysis": []
        }
      ]
    }
  ],
  "assets": [
    {
      "id": "img_p0008_i00_stem_01",
      "source": "crops/p0008-i00-stem-01.png",
      "questionRef": "p0008-i00",
      "target": "stem",
      "sourcePage": 8,
      "sourceBbox": [420, 680, 1480, 1320],
      "confidence": 0.95,
      "figureQuality": {
        "cropMethod": "shape-candidates",
        "candidateIds": ["p0008-c001", "p0008-c002"],
        "sourceShapeIds": ["7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32", "33", "34", "35", "36", "37"],
        "paddingRatio": 0.04,
        "background": "white",
        "cropMetadata": "crops/p0008-i00-stem-01.figure.json",
        "includedTextLabels": ["A", "B", "C", "D", "E", "a", "b", "c", "图1", "图2"],
        "semanticReview": {
          "labelsVerified": true,
          "lineEndpointsVerified": true,
          "unrelatedContentExcluded": true,
          "localCropOpened": true
        },
        "visualReview": {
          "status": "passed",
          "reviewedAgainstPage": true
        }
      }
    }
  ],
  "recognitionEvidence": "recognition-evidence.json",
  "figureCandidates": "figure-candidates.json",
  "figureValidationReport": "figure-validation-report.json",
  "figureCandidateExclusions": [
    {
      "candidateId": "p0024-c001",
      "sourcePage": 24,
      "reason": "候选框实际为题干公式文本组，不是独立配图",
      "visualReview": {
        "status": "passed",
        "reviewedAgainstPage": true
      }
    }
  ],
  "warnings": [],
  "needsReview": []
}
```

## Required invariants

- `protocol` must be `qulifang-ppt-tex` and `version` must be `1`.
- `source.pageCount` must equal the number of unique page records.
- `pages` must contain the continuous sequence `1..pageCount`, exactly once each.
- `pageType` must be `lecture` or `question`.
- A lecture page must have an empty `questions` array.
- A question page contains exactly one question item by default. Keep numbered parts such as `（1）（2）（3）` or `①②③` inside that question's `sourceText` and `stem`.
- A page may contain multiple question items only when it visibly contains multiple independent top-level questions with no shared premise, diagram, answer context, or source-text range. Such a page must include a validated `questionGrouping` record.
- `itemIndex` starts at `0` on each page and remains continuous.
- `ref` must be globally unique. Use `pNNNN-iNN`, for example `p0008-i00`.
- Every question must contain non-empty `sourceText`: an exact transcription of all wording and options in that question region before LaTeX conversion. Do not include slide titles, footers, page numbers, diagram labels already preserved in the figure, or unrelated commentary.
- `sourceTextOrigin` must be `native` or `visual`. Use `native` only when the transcription follows `page.nativeText` in order; use `visual` for scanned/image-only or incomplete OOXML text after checking the rendered page.
- `recognitionEvidence` must point to the local `qulifang-ppt-recognition-evidence/v1` file generated for the same source hash and page count.
- A native question must contain one or more `sourceEvidence` entries. Each entry selects the exact Python-style `[start:end]` range of a text block; joining selected ranges with newlines must equal `sourceText` exactly. A visual question leaves `sourceEvidence` empty.
- Every question must have a passing `textReview`, must be compared with the rendered page, and must confirm formulas and option order in at least two passes.
- Generated `stem` plus `options` must preserve `sourceText`; `build_question_ir.py` blocks packaging below its source-fidelity threshold. Do not make `sourceText` match a paraphrase—correct both fields from the slide.
- Final question numbering is generated from page order and item order. `sourceNumber` is evidence only.
- `type` must be `选择题`, `填空题`, or `应用题`.
- Every asset ID must be globally unique and referenced exactly once by a content block.
- Every asset must belong to the question referenced by `questionRef`.
- When assets exist, `figureValidationReport` must point to a passing local `qulifang-ppt-figure-validation/v1` report covering exactly those asset IDs.
- When assets exist, `figureCandidates` must point to the exact local `qulifang-ppt-figure-candidates/v1` file used to render them.
- Every diagram-like candidate on a question page must be selected by an asset or explicitly accounted for in `figureCandidateExclusions`.
- Each `figureCandidateExclusions` record must reference an existing candidate on the same `sourcePage`, provide a concrete visual reason, and contain a passing `visualReview`. Do not exclude a companion figure merely because the wording explicitly names another figure.
- All asset paths must be local paths relative to `ppt-analysis.json` or absolute paths.
- Do not include URLs, Base64 data, SVG, or executable content.

## Page classification

Classify as `question` when the page asks the learner to produce an answer, choose an option, fill a blank, calculate, prove, judge, or solve. Classify worked examples, definitions, summaries, derivations presented as instruction, and answer-only explanations as `lecture` unless they clearly retain a learner-facing prompt.

When a page mixes context and an explicit learner task, classify it as `question` and preserve all narrative, premises, examples, and parenthetical material inside the question region. Exclude only material that is visually separate and demonstrably unrelated to solving the task. Never shorten contextual wording merely because the final sentence contains the direct instruction.

## Question grouping

Use one page-level question by default. Treat `（1）（2）（3）`, `(1)(2)(3)`, `①②③`, several blanks, and several requested calculations under one shared premise as subquestions of the same question. Preserve their original order and line breaks in one `sourceText` and one `stem`; do not create repeated page tabs or separate question numbers for them.

Allow multiple question objects on one page only when the rendered page shows genuinely independent top-level questions, for example distinct `例1` and `例2` blocks that do not share a common stem or diagram. Record the exception on the page:

```json
"questionGrouping": {
  "mode": "multiple",
  "reason": "页面明确包含两个互不依赖的例题区块",
  "independentTopLevelLabels": ["例1", "例2"]
}
```

For a multiple-question exception:

- provide one visible independent top-level label for every question;
- keep source evidence ranges disjoint;
- never split an item whose later part begins with a subquestion marker;
- never repeat a common premise in several question objects;
- add a blocking `question_boundary` review record when independence is uncertain.

## Question type normalization

- Single-choice and multiple-choice → `选择题`.
- Fill-in and true/false → `填空题`; represent true/false answers as `√` or `×` only when present in the source.
- Calculation, solution, proof, and application → `应用题`.

Do not default every question to `选择题`. When evidence is insufficient, choose the closest editable type and add a `question_type` review record.

## Content blocks

Sections `stem`, each option, `answer`, and `analysis` accept these blocks:

```json
{"kind": "text", "value": "ordinary text"}
{"kind": "latex", "value": "$x^2$ or \\[x^2\\]"}
{"kind": "image", "assetId": "img_p0008_i00_stem_01"}
```

Use `options` keys `A`, `B`, `C`, `D` in source order. Image-only options are valid. Leave absent answers and analyses as empty arrays.

Content blocks do not automatically imply paragraph breaks. Adjacent `text`/`latex` blocks in a sentence are normalized into one continuous protocol paragraph. Put an explicit `\n\n` inside a text value only when the source visibly starts a new paragraph. Keep display equations in their own block with `\[...\]`.

## Images

Use target values:

- `stem`
- `option:A`, `option:B`, `option:C`, `option:D`
- `answer`
- `analysis`

Prefer a semantic crop derived from selected OOXML Shape candidates for geometry, graphs, charts, complex tables, apparatus, and composed PowerPoint shapes. Embedded PPT media is evidence, but it may not contain labels or lines added as separate shapes.

When the source question region contains `图1/图2`, comparison, before/after, construction-step, or derivation figures, preserve every member. Prefer one asset rendered from multiple candidates when their relationship or relative order matters; otherwise insert separate assets in source reading order. A phrase such as `利用图2` does not permit removal of `图1` when the page also says `参照上述证法` or otherwise relies on the first figure as context.

Every retained figure must:

- use a pure or near-pure white border/background;
- retain all labels, axes, legends, units, line endpoints, and arrowheads;
- exclude the slide title, question paragraph, footer, page number, logo, and unrelated nearby content;
- keep 2–8% padding, normally 4%;
- be visually reviewed against the annotated full slide before final validation.
- preserve all labels attached by selected candidates and match the candidate Shape IDs exactly;
- reference the generated `.figure.json` using `figureQuality.cropMetadata` and require `renderCoverageRatio >= 0.995`;
- complete `semanticReview` for labels, line endpoints/arrowheads, unrelated-content exclusion, and opening the local full-resolution crop.

Use `figureQuality.cropMethod` values:

- `shape-candidates`: crop generated from one or more candidates produced by `extract_figure_candidates.py`; require `candidateIds`, `sourceShapeIds`, `includedTextLabels`, and `cropMetadata`.
- `powerpoint-selection`: PowerPoint-native export of an explicitly selected Shape group.
- `raster-semantic`: fallback for a source figure that is already a raster image; isolate it on white and record a review item when boundaries remain uncertain.

Set `visualReview.status` to `passed` only after comparing both the candidate-overlay page and the white-background crop. Do not use a hand-estimated pixel rectangle as the primary crop method.

## Review records

Use structured records:

```json
{
  "pageNo": 8,
  "questionRef": "p0008-i00",
  "type": "formula_recognition",
  "message": "分母符号较模糊，需要人工确认",
  "blocking": false
}
```

Recommended types:

- `page_type`
- `question_boundary`
- `question_type`
- `formula_recognition`
- `image_assignment`
- `option_order`
- `answer_ownership`
- `content_integrity`

Set `blocking: true` only when a faithful import package cannot be produced without correction.
