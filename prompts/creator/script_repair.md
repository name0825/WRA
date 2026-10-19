# WRA Draft Script Repair

The site creator wrote unsupported Chinese characters (Hanja/Han) or Japanese kana into Korean text.

Each entry in `Texts` below has an `index`, the `text` as written, and `foreign`, the offending characters found in it.

Rewrite every `text` in Korean (Hangul) with the same meaning. Change only the part written in the foreign script. Keep proper nouns, numbers, URLs, punctuation, and every other word exactly as they are.

Return every `index`. Replace only the characters listed in `foreign` with Korean text. Preserve all other text exactly, including legitimate Han/kana names supplied in the brief and URL contents. Do not remove or translate those preserved names.

Output exactly one JSON object, `{"texts": [{"index": 0, "text": "..."}, ...]}`, with no fence, commentary, or other text.
