# WRA Script-Mixing Repair

The generator wrote Hanja, kana, or stray Latin letters into what should be pure Korean text.

For each entry in `Tokens` below, `token` is the offending text as it appears on the page, `sentence` is the surrounding sentence for context, and `source_context` is source text that shares Hangul characters with the token and may already contain the correct wording.

Return a JSON object mapping each `token` exactly (same string, same characters) to a Korean-only replacement that preserves the intended meaning. Prefer the wording found in `source_context` when it covers the token's meaning; otherwise write natural Korean yourself.

The replacement must contain only Hangul, along with any punctuation, spaces, or digits already present in the token. Never include Hanja, kana, or Latin letters in the replacement.

Output exactly one JSON object, `{"token": "replacement", ...}`, with no fence, commentary, or other text.
