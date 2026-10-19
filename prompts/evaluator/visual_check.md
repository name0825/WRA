# WRA Visual Inspection

You are shown full-page screenshots of one web page, one per viewport. Look only at what is rendered in the images. You have no source code; do not guess at causes.

Report every rendering defect you can actually see, of these kinds:

- `unreadable_text`: text whose colour is too close to its background to read comfortably (for example dark blue on dark navy, light grey on white).
- `overlapping_elements`: elements drawn on top of each other so that one covers part of another, especially a heading or text covered by a card, image, or header.
- `clipped_text`: text cut off at a container edge or the screen edge, or truncated mid-word.
- `empty_gap`: a large blank area that is clearly unintended, such as a section-sized empty band between content blocks.
- `broken_image`: a missing-image icon, an empty image frame, or alt text shown in place of an image.
- `raw_markup`: text rendered as raw code or markup, such as visible HTML tags, CSS, template placeholders, or escaped entities.
- `broken_layout`: content squeezed into a narrow strip or pushed below so most of the screen is empty, text wrapping one or a few characters per line, or content running past the screen edge.

For each defect give the viewport name from the list below, the kind, a one-sentence description naming the affected element and its visible text, and a severity:

- `high`: a visitor would clearly notice it and it hurts reading or using the page.
- `low`: minor or debatable.

Deliberate design choices (dark themes, generous whitespace, decorative overlaps that leave all text readable) are not defects. If you see no defects, return an empty `defects` list. Do not report anything you cannot point to in an image.

Return only JSON of the form `{"defects": [{"viewport": "...", "kind": "...", "description": "...", "severity": "high"}]}`.
