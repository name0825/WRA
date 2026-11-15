# WRA Source Page Impression

You are looking at a full-page screenshot of the website that is about to be redesigned.
Report how the page reads as a whole. This is the record of what the redesign inherits and
what it should improve, so be concrete and honest about both.

Return exactly one JSON object and nothing else:

```json
{
  "mood": ["3 to 5 single-word descriptors of the overall visual character"],
  "density": "one short phrase on information density and whitespace",
  "layout": "one sentence naming the dominant layout pattern and reading flow",
  "strengths": ["2 to 4 short phrases worth carrying into the redesign"],
  "weaknesses": ["2 to 4 short phrases the redesign should fix"]
}
```

Rules:
- Judge only what is visible. Do not speculate about code, performance, or intent.
- `strengths` and `weaknesses` are design observations (hierarchy, contrast, rhythm,
  alignment, balance), not content opinions.
- Write every value in English, at most 12 words each.
- Keep the whole object under 90 words. It is carried in every later prompt.
