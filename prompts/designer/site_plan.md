# WRA Site Restructure Planner

The block above lists the source site's pages and what each one actually contains.
Content freedom is `madmax`, so the page structure itself is open: the redesign is not
obliged to end up with the same pages the source had.

Decide the page set the redesigned site should have.

You may:
- **Keep** a page as it is, when it already has one clear job.
- **Merge** two or more source pages into one, when they are thin, overlapping, or read as
  one subject split apart for no reason.
- **Split** one source page into several, when it carries several unrelated jobs that each
  deserve their own page and enough content to fill one.

## Rules

- Every source page must be listed in at least one output page's `sources`. Content may be
  reorganised; it may not be dropped by restructuring.
- Never invent a page that the source content cannot fill. A page needs real material, not
  a heading and a sentence. If you cannot say what fills it, do not create it.
- Prefer keeping the structure. Restructure only where you can name the reason. A site of
  well-separated pages should come back unchanged.
- `slug` is lowercase ASCII letters, digits, and hyphens. It becomes the file name.
- Exactly one page has `"entry": true`. That is the site's front page, and it must be
  listed first.
- `content_focus` tells the later design step which part of the source material this page
  is responsible for. It matters most when several pages share one source: without it they
  will repeat each other.
- Write `title` in the language the source content uses. Write the other fields in English.

## Output

Return exactly one JSON object and nothing else:

```json
{
  "pages": [
    {
      "slug": "index",
      "title": "page title in the source language",
      "entry": true,
      "sources": ["source page slug", "..."],
      "content_focus": "which material this page carries",
      "reason": "why this page exists in this shape"
    }
  ]
}
```

Keep every string short. `reason` is one clause, not a paragraph.
