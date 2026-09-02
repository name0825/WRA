# WRA Browser Evaluation

Evaluate the generated web page using both the provided structured data and browser-rendered screenshots when available.

The evaluation dimensions are:

- design_completeness
- visual_hierarchy
- design_consistency
- content_preservation
- ux
- readability
- responsive_design
- originality
- html_quality

Score every dimension from 0 to 10 and provide concise evidence in that dimension's `feedback` field. Keep each dimension's `feedback` to 1–2 sentences.

## Score calibration

Use the full 0–10 scale. Do not inflate scores simply because the page is functional or visually polished.

- **0–2:** fundamentally broken, absent, or severely fails the dimension
- **3–4:** major deficiencies; the dimension is only partially satisfied
- **5–6:** adequate or mixed; noticeable weaknesses remain
- **7–8:** strong implementation with limited, non-trivial weaknesses
- **9:** excellent; only minor issues remain
- **10:** exceptional and fully convincing for the dimension; use only when the evidence clearly supports it

A score must reflect the supplied evidence, not an assumed quality level. If evidence is insufficient for a claim, do not invent evidence; judge only what can be established from the supplied inputs.

Browser evidence is especially important for UX, readability, responsive_design, and visual_hierarchy. Consider horizontal overflow, JavaScript errors, page errors, and differences between desktop, tablet, and mobile screenshots.

When browser evaluation is unavailable, disabled, or `browser_evaluation` is null, do not assume that screenshots exist and do not penalize the page merely because visual browser evidence is unavailable. For `responsive_design`, use the DesignSpec's declared responsive strategy, breakpoints, and behavior together with the generated HTML structure. For `ux`, `readability`, and `visual_hierarchy`, judge only what can be established from the DesignSpec and generated HTML structure. In the relevant feedback, state that no browser-rendered visual confirmation was available when that limitation materially affects the assessment.

## Content preservation scope

For `content_preservation`, evaluate semantic fidelity: whether the generated page retains the original information, meaning, emphasis, important links/media, and required user-visible content while allowing layout and visual expression to change.

Do not treat deterministic exact-match validation as the sole definition of this dimension. Exact presence checks are handled separately by WRA validators; this evaluator should judge semantic completeness, prominence, context, and meaningful preservation based on the Feature Model and generated page.

## Output contract

Return JSON only. Each evaluation dimension MUST be an object with exactly these fields: `score` (number from 0 to 10) and `feedback` (string). Do not return a bare number for a dimension. Use `summary` for the overall evaluation and `improvement_suggestions` for actionable improvements.

`overall_score` MUST equal the arithmetic mean of the nine dimension scores. Before returning, calculate the nine scores, sum them, divide by 9, and verify that the value written to `overall_score` is exactly that result. Do not estimate or independently choose `overall_score`.

Keep `summary` concise and keep each `improvement_suggestions` item concise so the complete evaluation remains within the available output budget.

Example shape:

{
  "design_completeness": {"score": 8, "feedback": "..."},
  "visual_hierarchy": {"score": 8, "feedback": "..."},
  "design_consistency": {"score": 8, "feedback": "..."},
  "content_preservation": {"score": 8, "feedback": "..."},
  "ux": {"score": 8, "feedback": "..."},
  "readability": {"score": 8, "feedback": "..."},
  "responsive_design": {"score": 8, "feedback": "..."},
  "originality": {"score": 8, "feedback": "..."},
  "html_quality": {"score": 8, "feedback": "..."},
  "overall_score": 8,
  "summary": "...",
  "improvement_suggestions": ["..."]
}

Do not invent browser observations when browser evaluation is disabled.

The generated page should preserve the original content while allowing its layout and visual expression to be redesigned. Evaluate the implementation against the DesignSpec rather than expecting a fixed page template.
