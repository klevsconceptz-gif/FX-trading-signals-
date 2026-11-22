# Fonts

Self-hosted copies of DM Sans, IBM Plex Mono and Manrope (Latin and Latin Extended subsets, WOFF2 and WOFF).

- Source: the `@fontsource` npm packages, version 5.3.0 (`@fontsource/dm-sans`, `@fontsource/ibm-plex-mono`, `@fontsource/manrope`).
- Licence: SIL Open Font License 1.1 (each package ships its LICENSE file).
- Used by `static/fonts.css`, which every HTML page loads instead of Google Fonts.
- `font-display: optional` is deliberate: a font that arrives late is not swapped in, so the layout does not shift (Lighthouse CLS).
