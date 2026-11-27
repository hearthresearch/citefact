# Changelog

All notable changes to citefact. `report.json` carries `schema_version`; any change to its shape is recorded here.

## Unreleased

### Added

- Web pages can be sources. A bibliography entry with no PDF is checked against a saved copy of the page: a Zotero snapshot attachment (`text/html`), or an `.html`/`.htm` file in the `--pdfs` folder, matched by filename stem or by its `<title>` like a PDF. The page's visible text is read with the standard library (scripts, styles and other non-rendered elements dropped, character references decoded), so the quote and claims checks run on it unchanged. Nothing is fetched: the check stays offline, and the cited text is what the page said when it was saved. When an entry has both, the PDF is used.

### report.json schema 4

- `sources[]` carry `snapshot`: the path of the saved web page read for that source, or null. Its `conversion.converter` is `citefact-html`, with `text_layer` null.

## 0.3.0 (2026-09-15)

### Added

- The quotes check now sees Markdown `>` block quotes, which is how APA sets quotations of 40+ words: indented, without quotation marks, and therefore invisible to the old scan for text between marks. The longest quotations in a manuscript were never checked. A block is compared whole with its source; the closing `(Author, year, p. n)` inside the block is treated as the attribution and left out of the comparison. Text inside code fences, indented code and tables is no longer scanned for quotes. The manuscript is parsed with `markdown-it-py` (new direct dependency, already installed transitively through `rich`).

- Page locators are read from citations (`(Smith, 2023, p. 47)`, `Smith (2023, pp. 47–49)`, `para. 3`) and checked against where the quote was found. Every matched quote now reports its PDF page, which the HTML report links to (`#page=N`), and its printed page number when the PDF's numbering can be established: from bare page numbers in the running headers or footers (at least three pages, a clear majority agreeing on the offset), else from the bibliography entry's page range when it spans exactly the PDF's pages. A quote found verbatim on a different page than the citation names is a new `quote_page_mismatch` warning; unknown numbering never raises one.

### Fixed

- The claims check no longer judges most papers on half their text. One source reached the model cut to 40,000 characters, roughly half a typical journal article converted to markdown, and the cut was silent: a claim supported in a discussion section came back `not_in_paper` with high confidence, which reads as a caught fabrication and was an artefact of the budget. The budget is now 300,000 characters (about a long article or a thesis chapter, whole), overridable with `CITEFACT_MAX_SOURCE_CHARS`.
- A citation inside an inline quotation (`"... as Galal (2001) had" (Smith, 2023)`) no longer counts as the quote's attribution: it is the quoted author's, and the quote was being checked against that paper instead of the one cited after the closing mark.
- Parenthetical citations were located one character too early: the span reported for `(Smith, 2023)` started at the opening parenthesis and stopped one character short of the year. Line numbers were unaffected in practice; the span now covers exactly the author-year item.
- Truncation, when it still happens, is always reported: the affected source ids in `summary.sources_truncated`, a `source_truncated` flag with the character counts on every verdict formed on a cut source, a banner and a per-claim badge in the HTML report, and a log warning during the run.

### Changed

- Verdict cache entries from before this change are invalidated (`PROMPT_VERSION` 2), since they were formed on less of the source than the same run would send today. The truncation budget is part of the cache key only when it actually bit, so raising it re-verifies the sources it affected and leaves the rest cached.

### report.json schema 3

- `findings[]` of level `quotes` carry `form`: `inline` (text between quotation marks) or `block` (a `>` block quote).
- `findings[]` of level `quotes`: new type `quote_page_mismatch` (warning). Matched quotes (`quote_verified`, `quote_modified`, `quote_page_mismatch`) carry `pdf_page` (1-based page of the match in the PDF) and `page_found` (printed page number, or null when unknown); any quote whose citation had a page locator carries `page_claimed` (list of ints). Keys are absent when the source has no page structure or the citation no locator.
- `summary.sources_truncated`: list of source ids whose text was cut to the context budget before the model read it. Empty on a clean run.
- `findings[]` of type `claim_verdict` carry `source_truncated: true` plus `source_chars` and `source_chars_used` when the verdict rests on a partial reading; the keys are absent otherwise.

## 0.2.1 (2026-09-06)

### Changed

- Help text rewritten for agents: every command's `--help` states what it writes, its exit codes, what goes to stdout and stderr, and the `report.json` contract (finding types and severities per level); every option describes its allowed values. New `citefact help [COMMAND]` subcommand and `-h` alias. No behaviour change; `report.json` stays at schema 2.

## 0.2.0 (2026-09-06)

### Changed

- PDF conversion now goes through [pagebound](https://pypi.org/project/pagebound/) instead of a private Docling wrapper. Converted PDFs live in `.citefact/pagebound/` (one object per PDF hash and converter version). If a shared pagebound cache exists (`~/.cache/pagebound`, or `PAGEBOUND_CACHE`), citefact reads it first and copies hits into the project store; it never writes to it.
- Scanned PDFs without a text layer are OCR'd instead of failing, and the report marks them.
- PDFs that keep failing to convert are tombstoned by pagebound and skipped on later runs; `--force` retries them.
- The old `.citefact/cache/sources/*.md` files are no longer read and can be deleted.

### report.json schema 2

- `sources[].conversion`: `{converter, converter_version, converted_at, text_layer}` for a converted source, `null` otherwise. `text_layer` is `false` when the text came from OCR.

## 0.1.0 (2026-08-17)

Initial release: citations, quotes and claims checks; Markdown manuscripts; BibTeX and PDF folder or Zotero collection as sources; self-contained HTML report plus `report.json` (schema 1).
