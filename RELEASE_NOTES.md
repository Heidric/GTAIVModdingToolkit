# GTA IV Modding Toolkit 0.16.0

## Highlights

- Added a generic archive browser for GTA IV RPF2, RPF3, and IMG3 archives.
- Added export and transactional replacement for existing archive entries.
- Added WTD texture inspection, preview, DDS export, and replacement.
- Added embedded WDR texture inspection, preview, PNG/DDS export, and replacement.
- Added queued multi-texture replacement across multiple WTD and WDR entries in one archive transaction.
- Added bounded archive-backup retention while permanently preserving the first backup.
- Reorganized runtime, packaging, and documentation files into clearer project directories.

## Archive browser

The archive browser opens `.rpf` and `.img` archives, displays nested entries, and supports a case-insensitive name filter without rereading the archive. RPF2 filenames are decoded from the archive string table, RPF3 filenames use known hash mappings, and IMG3 entries expose their stored names and resource metadata.

Existing entries can be exported or transactionally replaced. Replacement preserves the entry set and unrelated metadata. Payloads that fit remain in place; larger payloads are relocated to an aligned end-of-file offset where the archive format permits it. Staged and committed archives are reopened and verified before success is reported.

This release does not add, delete, rename, or move archive entries and does not edit archive directory structures.

## WTD and WDR texture tools

Selecting a WTD entry exposes its texture dictionary, including stable index, name, dimensions, format, mip count, payload size, and replacement support. Selecting a supported WDR entry exposes its embedded texture dictionary through the drawable shader group.

Extractable textures can be previewed, exported as DDS, and exported as PNG where decoding is available. Existing DXT1, DXT5, and A8R8G8B8 payloads can be replaced from common image formats accepted by Pillow and texfury.

Replacement preserves the surrounding RSC5 resource structure, texture records, dimensions, formats, mip counts, and unrelated payload bytes. It does not add textures or resize texture tables.

## Queued texture replacement

Multiple texture replacements can be queued across different WTD and WDR entries inside the currently opened archive. The review dialog shows the current texture and replacement-image previews, allows individual removals and queue clearing, and replaces an existing queue item when the same target is selected again.

Applying the queue performs one archive transaction:

1. Every request is validated before the archive is modified.
2. Requests are grouped by archive entry.
3. Each affected WTD or WDR entry is read, patched, and recompressed once.
4. All modified entries are written to one staging archive.
5. The staged archive and every replacement are verified.
6. One backup is created and one atomic commit replaces the active archive.

Any failure leaves the active archive unchanged and keeps the queue available for correction or retry.

## Transaction safety and backups

- All mutations are performed against staging files.
- Staged entry sets and unrelated metadata must remain unchanged.
- Replacement bytes are verified before commit and after commit.
- A verified backup is created before the active archive is replaced.
- A failed final swap or final verification restores the original archive.
- The browser serializes export and replacement operations and blocks application shutdown while workers are active.
- The first backup of each archive is preserved permanently.
- A configurable number of newer rolling backups is retained; the default is three.

## Existing audio and radio-logo workflows

The transactional single-track, batch audio, audio recovery, radio-logo installation, and radio-logo recovery workflows remain available. Audio input supports MP3, WAV, OGG, FLAC, AAC, and M4A through FFmpeg and pydub.

## Scope limitations

- No new radio stations or track slots.
- No new, deleted, renamed, or moved archive entries.
- No archive directory-structure editing.
- No new or deleted WTD/WDR texture records.
- No texture-table resizing.
- No generic model geometry, collision, or localization editing.
- Texture batching is limited to one currently opened archive at a time.
- Episodes from Liberty City compatibility remains unvalidated.
