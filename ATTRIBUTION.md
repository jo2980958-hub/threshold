# Where Threshold's typefaces come from

Threshold's design was rebuilt to follow a shared internal design language, referred
to here as **Transmission** — same tokens, same shell, same component set. That
design language names two faces: Inter for every UI face and body sentence, and
JetBrains Mono for every clock, timestamp and tabular figure. Both replace the
single serif this app vendored in the previous pass.

## What was here before

`threshold/web/fonts/EBGaramond.woff2` and `EBGaramond-Italic.woff2`, an old-style
garalde book face chosen for a graphite-and-brass household-ledger theme. That theme
is gone; so are the two files. Nothing of the previous rationale (recorded in this
file's git history) still applies to the current design.

## What is here now

| In the app | File | Weight | Style | Licence | Source |
| --- | --- | --- | --- | --- | --- |
| `threshold/web/fonts/Inter.woff2` | Inter | 400–800 (variable) | Normal | SIL Open Font License 1.1 | [Google Fonts: Inter](https://fonts.google.com/specimen/Inter) |
| `threshold/web/fonts/Inter-Italic.woff2` | Inter | 400 | Italic | SIL Open Font License 1.1 | [Google Fonts: Inter](https://fonts.google.com/specimen/Inter) |
| `threshold/web/fonts/JetBrainsMono.woff2` | JetBrains Mono | 400–700 (variable) | Normal | SIL Open Font License 1.1 | [Google Fonts: JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono) |

All three files are the Latin-subset `woff2` Google Fonts itself serves (the same
`unicode-range` Google's own CSS ships for the "latin" subset: `U+0000-00FF, U+0131,
U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329,
U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD`),
fetched directly from `fonts.gstatic.com` and committed into this app so the kiosk
and the judge's browser both render the same file with no network call at request
time. The reference design language links Inter and JetBrains Mono from Google's
own CDN; Threshold vendors them instead, the same reasoning applied elsewhere for
vendoring Public Sans and Source Serif 4, and the reasoning the previous EB
Garamond pass gave here.

Inter and JetBrains Mono are both distributed as variable fonts. Google Fonts serves
one file per style axis (`wght 400..800` for Inter's upright cut, one weight for its
italic cut, `wght 400..700` for JetBrains Mono) rather than a separate static file per
named weight; `threshold.css`'s `@font-face` blocks declare the weight range
(`font-weight: 400 800` / `400 700`) against that single file each, which is the
correct way to register a variable font for several weights without shipping five
copies of it.

The italic file is kept because the app still sets `font-style: italic` in two
places — `.empty-note` and `.note blockquote`, the household's own quoted words —
and without it those two spots would synthesise a faux-italic instead of using the
type designer's real one.

The SIL Open Font License permits embedding, redistribution and modification; the full
licence text is at <https://openfontlicense.org/>. Nothing in any of the three files
was altered beyond the Latin subsetting Google Fonts itself already applies.

`--sans` and `--mono` (`threshold.css`) carry the same system-font fallback chains
the reference design language declares after Inter and JetBrains Mono, so the
stated fallback is honest about what happens if a vendored file somehow fails to
load.
