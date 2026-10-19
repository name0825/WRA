# WRA Script-Mixing Repair

The generator introduced Hanja, kana, or stray Latin letters into Korean text. Source-backed names and legitimate mixed-language wording must be preserved.

For each entry in `Tokens` below, `token` is the offending text as it appears on the page, `sentence` is the surrounding sentence for context, `source_context` is relevant source text, and `preserve_literals` lists source-backed strings that must survive unchanged.

Return a JSON object mapping each `token` exactly (same string, same characters) to a replacement that repairs only the corrupt span and preserves the intended meaning. Prefer the wording found in `source_context` when it covers the token's meaning; otherwise write natural Korean for the corrupt span.

Write repaired spans in Hangul. Keep legitimate source-backed Latin names, Hanja, kana, punctuation, spaces, and digits unchanged; do not remove or translate them merely because the token contains corruption. For example, when the source contains `Park의 제조`, repair `Park의製造` to `Park의제조`, preserving `Park`.

Output exactly one JSON object, `{"token": "replacement", ...}`, with no fence, commentary, or other text.
