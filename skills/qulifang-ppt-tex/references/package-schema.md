# Courseware package schema

The final ZIP uses `qulifang-ppt-tex/v1` and is accepted by the EducationApp courseware “代码上传” entry.

When `build_courseware_package.py` is called without `--output`, it writes `SOURCE-STEM-ppt-code-import.zip` into the source PPTX directory. An explicit `--output` overrides this default.

```text
manifest.json
ppt-analysis.json
ppt-validation-report.json
figure-validation-report.json  # required when question figures exist
source/source.pptx
question-package.zip       # omitted only when there are no question pages
```

`manifest.json` contains:

```json
{
  "protocol": "qulifang-ppt-tex",
  "version": 1,
  "title": "勾股定理",
  "source": {
    "filename": "勾股定理.pptx",
    "path": "source/source.pptx",
    "sha256": "64 lowercase hex characters",
    "pageCount": 12
  },
  "analysis": "ppt-analysis.json",
  "validationReport": "ppt-validation-report.json",
  "figureValidationReport": "figure-validation-report.json",
  "questionPackage": "question-package.zip",
  "questionMap": [
    {
      "pageNo": 8,
      "itemIndex": 0,
      "questionRef": "p0008-i00",
      "questionNumber": "1"
    }
  ]
}
```

`questionMap` covers every question in `ppt-analysis.json` exactly once. Multiple page items may reference the same `questionNumber` when the deterministic TEX builder merged identical question content. Lecture pages have no map record.

The builder verifies that `figure-validation-report.json` covers every figure, records the current figure hashes, and has passed white-background, padding, edge-clipping, provenance, and visual-review gates. The backend verifies ZIP paths and size limits, the source PPTX digest/page count, the complete page sequence, blocking review records, every question mapping, and the inner `educationapp-question-tex/v1` package before writing anything.
