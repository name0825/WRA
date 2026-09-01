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

Score every dimension from 0 to 10 and provide concise evidence in that dimension's `feedback` field.

Browser evidence is especially important for UX, readability, responsive_design, and visual_hierarchy. Consider horizontal overflow, JavaScript errors, page errors, and differences between desktop, tablet, and mobile screenshots.

Return JSON only. Each evaluation dimension MUST be an object with exactly these fields: `score` (number from 0 to 10) and `feedback` (string). Do not return a bare number for a dimension. Use `summary` for the overall evaluation and `improvement_suggestions` for actionable improvements.

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

`overall_score` must be the arithmetic mean of the nine dimension scores. Do not invent browser observations when browser evaluation is disabled.

The generated page should preserve the original content while allowing its layout and visual expression to be redesigned. Evaluate the implementation against the DesignSpec rather than expecting a fixed page template.
