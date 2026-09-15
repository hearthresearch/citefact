# Changelog

All notable changes to citefact. `report.json` carries `schema_version`; any change to its shape is recorded here.

## Unreleased

### Added

- The quotes check now sees Markdown `>` block quotes, which is how APA sets quotations of 40+ words: indented, without quotation marks, and therefore invisible to the old scan for text between marks. The longest quotations in a manuscript were never checked. A block is compared whole with its source; the closing `(Author, year, p. n)` inside the block is treated as the attribution and left out of the comparison. Text inside code fences, indented code and tables is no longer scanned for quotes. The manuscript is parsed with `markdown-it-py` (new direct dependency, already installed transitively through `rich`).

### Fixed

- The claims check no longer judges most papers on half their text. One source reached the model cut to 40,000 characters, roughly half a typical journal article converted to markdown, and the cut was silent: a claim supported in a discussion section came back `not_in_paper` with high confidence, which reads as a caught fabrication and was an artefact of the budget. The budget is now 300,000 characters (about a long article or a thesis chapter, whole), overridable with `CITEFACT_MAX_SOURCE_CHARS`.
- Parenthetical citations were located one character too early: the span reported for `(Smith, 2023)` started at the opening parenthesis and stopped one character short of the year. Line numbers were unaffected in practice; the span now covers exactly the author-year item.
- Truncation, when it still happens, is always reported: the affected source ids in `summary.sources_truncated`, a `source_truncated` flag with the character counts on every verdict formed on a cut source, a banner and a per-claim badge in the HTML report, and a log warning during the run.

### Changed

- Verdict cache entries from before this change are invalidated (`PROMPT_VERSION` 2), since they were formed on less of the source than the same run would send today. The truncation budget is part of the cache key only when it actually bit, so raising it re-verifies the sources it affected and leaves the rest cached.

### report.json schema 3

- `findings[]` of level `quotes` carry `form`: `inline` (text between quotation marks) or `block` (a `>` block quote).
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
