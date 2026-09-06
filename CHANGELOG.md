# Changelog

All notable changes to citefact. `report.json` carries `schema_version`; any change to its shape is recorded here.

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
