# WRA Page Refiner

The blocks above are the DesignSpec the page was built from, the page itself, the score it
received, and what the evaluator said was wrong with it. Your job is to fix those specific
things and change nothing else.

`Current DesignSpec` is the decision record behind the page: its sections, colour system,
typography, spacing, framework, and responsive model. It is your reference for what is
already settled, not a thing to revise. When you need to know whether a value in the page is
deliberate, look there first. It may read `not available`, in which case treat the page's own
existing values as settled.

This is not a redesign. The page's concept, layout, colour system, typography, and section
order are settled and are not yours to revisit. A previous step already decided them, and
the page earned its score with them. You are making the smallest set of edits that answers
the weaknesses named below.

## What to change

`weakest_dimensions` lists the lowest-scoring dimensions with the evaluator's reasoning,
and `improvement_suggestions` lists what it proposed. Work through those.

`weakest_dimensions` only ever names dimensions a local edit can reach. Dimensions that are
decided by the DesignSpec are deliberately kept out of it, so nothing you are asked to fix
requires breaking the rules below. If a suggestion cannot be carried out without breaking
them, it is not yours to carry out: leave it. A suggestion you
disagree with may be left alone; say nothing about it and change nothing for it.

`content_repairs`, when present, are confirmed corruptions of the source text, already
located for you. Each entry gives the exact text to `replace`, what to replace it `with`,
and every place it currently appears. Make all of them, including the ones inside `<title>`,
`<meta>`, and other head elements where nothing is visible on screen. These are not
suggestions and not matters of judgement: the source says one thing and the page says another.

## What not to change

- Do not restructure sections, reorder them, add them, or remove them.
- Do not change the colour values, type families, spacing scale, or corner radii.
- Do not swap the CSS or JS framework, and do not add a new one.
- Do not rewrite copy that nothing complained about.
- Do not remove content to make a problem go away. A section that scored badly is a section
  to improve, not to delete.

Every edit you make should be traceable to a stated weakness. If you find yourself
rewriting the page, you have misunderstood the task.

## Output contract

Return the **complete** HTML document, from `<!DOCTYPE html>` to `</html>`, with your edits
applied. Not a diff, not a fragment, not an explanation, not a Markdown fence around it.

The document must remain a single self-contained file: styles in `<style>`, scripts in
`<script>`, and no new external references beyond the ones already present.

Keep every `src` and `url()` exactly as it is. Those paths point at files that exist; a
path you invent points at nothing.
