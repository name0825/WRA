# WRA Site Creator

The block above is everything the client said about the site they want, and it is
deliberately short. Your job is to turn it into the site's content: what the pages are,
what each one says, and what the visitor is meant to do.

There is no existing site. Nothing here is being preserved; everything is being decided.

## What you are deciding

- **The page set.** Give the site the pages its subject actually needs and the brief can
  fill. A single-service business is often one page. A catalogue with distinct product
  families may want a second. Do not pad the site with pages that would hold two sentences.
- **What each page says.** Real headings and real sentences about this subject, in the
  client's language. Not lorem ipsum, not placeholder brackets, not "Your Company Name".
- **What the visitor does.** Concrete calls to action that match how this business is
  actually contacted.

## What you may and may not invent

The brief is short, so most detail is yours to decide. Invent freely: section topics,
supporting copy, product groupings, the words on a button.

Do not invent **checkable facts the client did not give you**: no phone numbers, no email
addresses, no street addresses, no founding years, no client names, no certifications, no
prices, no statistics. If the brief gives a fact, use it exactly as given. If it does not,
write around the gap — a contact section can invite an enquiry without printing a number
that does not exist.

Copy every checkable fact the brief gives (phone and fax numbers, email addresses, street
addresses, opening hours, registration numbers) verbatim into `business`; dropping one is a
failure.

## Creative latitude

You may add qualitative content the brief does not contain: strengths and "why us" copy,
quality and service claims (quality assurance, reliable supply, fast and accurate handling,
superlatives such as 최고), feature bullets, value-proposition text, process steps, icon suggestions, as many
sections as the volume target below sets, and a contact form drawn as UI. Still forbidden: invented
numbers, dates, years, opening hours or holidays, certifications, client names, prices,
statistics, bracket placeholders such as [대표자명], offers or terms the brief does not make
(free, discounts, lowest price, rankings), and notes to yourself or source remarks such as (Based on text).

Volume target for the entry page (apply it to the entry page only; other pages stay as small as
their purpose needs):

- After the hero heading, write 5 to 7 headed content blocks. Every heading carries its own paragraph and/or list; no bare headings.
- One block per product or service category the brief names, each with its own list of the items the brief names (for example a cutting-tool category listing inserts, drills, end mills and holders), not one merged list.
- A strengths ("why us") block of exactly 3 items, each a list item written as "short title: one sentence". Qualitative only.
- An about block with 2 or 3 qualitative commitment bullets.
- A contact block that repeats the brief's contact facts verbatim, and a closing call to action.

A form is drawn only as an honest hand-off: a `mailto:` action using the supplied email, or
fields alongside the real phone and email. Never write a script or message claiming it was
sent (no alert('접수되었습니다'), no success banner) unless a backend is supplied.

Everything you write becomes the site's record of itself, and later steps are held to it.
A number you invent here is a number the finished page will state as fact.

## How it should read

- Write in the plain language this client's own customer would use, not industry abstraction.
- Active voice. A button names the outcome the visitor gets, not the mechanism behind it.
- Say what the business does rather than selling it. Specific beats superlative.
- A sentence that could sit unchanged on a competitor's site is not written yet. Replace it with one only this client could print.

## Language

Write every heading, paragraph, list item, and button label in the language the brief uses.
A Korean brief produces a Korean site. Keep proper nouns exactly as the brief spells them.

## Images

`available_images` lists the media the client supplied, with what each one shows. Use them
where they belong and leave the rest alone. If the list is empty, plan a site that reads
well without photography rather than describing images that do not exist.

## Output

Return exactly one JSON object and nothing else:

```json
{
  "site_type": "business | portfolio | landing | catalogue | blog | ...",
  "industry": "short phrase",
  "brand_voice": "short phrase describing how the copy should sound",
  "business": {
    "company_name": "string or null",
    "services": ["string"],
    "phone": ["053-000-0000, exactly as the brief gives it"],
    "email": ["name@example.com, exactly as the brief gives it"],
    "address": "street address exactly as the brief gives it, or null",
    "contact": ["label: value for other contact channels such as fax"],
    "business_information": ["label: value, e.g. 사업자등록번호, 팩스, 영업시간"],
    "cta": ["string"]
  },
  "pages": [
    {
      "slug": "index",
      "title": "page title in the brief's language",
      "entry": true,
      "purpose": "one line on what this page is for",
      "headings": ["the page's headings, in reading order"],
      "paragraphs": ["real sentences about this subject"],
      "lists": [["grouped items", "belonging together"]],
      "buttons": ["call to action label"],
      "images": ["assets/file.png"]
    }
  ]
}
```

Rules:
- Exactly one page has `"entry": true`, and it is listed first. Its slug is `index`.
- `slug` is lowercase ASCII letters, digits, and hyphens; it becomes the file name.
- Every page needs at least one heading and at least one paragraph. A page you cannot fill
  is a page that should not exist.
- `images` holds paths copied verbatim from `available_images`, or an empty list.
- `lists` and `buttons` may be empty. Do not manufacture a list to look thorough.
