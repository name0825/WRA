# WRA Draft Script Repair

The site creator wrote Chinese characters (Hanja/Han) or Japanese kana into text that must be pure Korean.

Each entry in `Texts` below has an `index`, the `text` as written, and `foreign`, the offending characters found in it.

Rewrite every `text` in Korean (Hangul) with the same meaning. Change only the part written in the foreign script. Keep proper nouns, numbers, URLs, punctuation, and every other word exactly as they are.

Return every `index`. The rewritten text must not contain any Chinese characters or kana.

Output exactly one JSON object, `{"texts": [{"index": 0, "text": "..."}, ...]}`, with no fence, commentary, or other text.
