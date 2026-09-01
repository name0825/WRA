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

Score every dimension from 0 to 10 and provide concise evidence in `feedback`.

Browser evidence is especially important for UX, readability, responsive_design, and visual_hierarchy. Consider horizontal overflow, JavaScript errors, page errors, and differences between desktop, tablet, and mobile screenshots.

Return JSON only with exactly the structure required by the EvaluationResult schema. `overall_score` must be the arithmetic mean of the nine dimension scores. Do not invent browser observations when browser evaluation is disabled.

The generated page should preserve the original content while allowing its layout and visual expression to be redesigned. Evaluate the implementation against the DesignSpec rather than expecting a fixed page template.
