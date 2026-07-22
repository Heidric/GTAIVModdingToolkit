# GTA IV Modding Toolkit

A Windows desktop toolkit for modifying Grand Theft Auto IV assets.

[![Tests](https://github.com/Heidric/GTAIVModdingToolkit/actions/workflows/tests.yml/badge.svg)](https://github.com/Heidric/GTAIVModdingToolkit/actions/workflows/tests.yml)
[![Portable Windows Build](https://github.com/Heidric/GTAIVModdingToolkit/actions/workflows/portable-windows.yml/badge.svg)](https://github.com/Heidric/GTAIVModdingToolkit/actions/workflows/portable-windows.yml)

<div style="display: flex; justify-content: space-between; margin: 20px 0;">
    <img src="assets/image.png" alt="Radio station selection interface" width="400"/>
    <img src="assets/image-1.png" alt="Radio track selection interface" width="400"/>
</div>

## Current scope

The toolkit modifies existing radio-track slots and existing radio-station logo textures. It does not create new stations or new track slots.

Implemented features:

- Browse GTA IV radio stations and their existing track slots.
- Preview extracted station tracks from the application.
- Replace one track at a time through a staged, verified transaction.
- Replace multiple tracks in one transactional batch.
- Automatically match batch input files to track slots by normalized filename.
- Review and change every batch mapping before processing.
- Prevent duplicate target slots within one batch.
- Update track durations in `sounds.dat15`.
- Preserve non-standard OAF timestamp and channel layouts when they cannot be safely rewritten.
- Remember the last directory used by game, audio, and other file pickers.
- Replace an existing station logo from PNG, WebP, JPEG, BMP, or TGA input.
- Preview selected and unselected logo variants before installation.
- Install radio-logo WTD changes transactionally in FusionFix or direct mode.
- Restore the previous complete radio-logo state from the application.
- Run a production preflight for dependencies, WTD sources, image input, and write access.
- Render the active in-game station logos in the station-selection page.
- Write rotating per-user application logs and create redacted support bundles from the start page.
- Detect common local GTA IV installations and remember the selected replacement method.

### Input formats

The single-track picker accepts:

- MP3
- WAV
- OGG

The batch picker additionally exposes:

- FLAC
- AAC
- M4A

Actual decoding is performed through FFmpeg and pydub, so support also depends on the installed FFmpeg build.

## Safety model

### FusionFix mode — recommended

FusionFix mode creates or updates override files under:

```text
<gtaiv>/update/pc/audio/sfx/
<gtaiv>/update/pc/audio/config/
```

The original files under `pc/audio/...` remain untouched.

### Direct replacement mode — risky

Direct mode modifies the original files under:

```text
<gtaiv>/pc/audio/sfx/
<gtaiv>/pc/audio/config/
```

Before modification, the toolkit creates timestamped backups next to the original RPF and `sounds.dat15` files.

### Transactional single-track replacement

Single-track replacement operates on staging copies of the selected station RPF and `sounds.dat15`:

1. The existing track is extracted and converted inside a temporary workspace.
2. Duration metadata is updated only in the staged `sounds.dat15`.
3. The converted track is packed only into the staged RPF.
4. The staged RPF is reopened and the replacement is extracted again for SHA-256 verification.
5. Direct mode creates timestamped backups only after staging and verification succeed.
6. The active RPF and `sounds.dat15` are replaced together; a failed final swap restores both previous files.

Cancellation is cooperative and is honored before the commit starts. A failed or cancelled operation does not leave a partial FusionFix override or a half-updated direct installation.

### Transactional batch replacement

Batch replacement operates on staging copies of both the selected station RPF and `sounds.dat15`:

1. Every selected source file is validated, extracted, and converted.
2. Duration metadata is updated in the staged `sounds.dat15`.
3. Every converted track is packed into the staged RPF.
4. Oversized entries are relocated instead of overwriting adjacent RPF data.
5. The staged RPF is reopened.
6. Every replacement is extracted again and compared with the packed source using SHA-256.
7. Active files are replaced only after all conversions and verification checks pass.

A failed or cancelled batch does not commit partial staged changes. If the final file swap itself fails, the worker restores rollback copies.

## RPF handling

The toolkit vendors a patched copy of `pyrpfiv` under `vendor/pyrpfiv/`.

When replacing an RPF entry:

- the existing name hash and TOC position are preserved;
- a replacement that fits remains at its current offset;
- a replacement that exceeds the current slot is appended at an `0x800`-aligned end-of-file offset;
- the encrypted or unencrypted RPF3 TOC is updated with the new size and offset;
- the new offset is constrained to the 31-bit RPF3 file-offset range.

## GTAIV.exe compatibility

The parser needs the GTA IV RPF AES key. The toolkit first checks known key offsets for established executable versions:

- 1.0.4.0
- 1.0.4r2
- 1.0.6.0
- 1.0.7.0
- 1.0.8.0
- 1.2.0.32
- 1.2.0.43
- 1.2.0.59

If those offsets do not match, the toolkit scans the selected `GTAIV.exe` for the same already-known key bytes at another location. This supports executable builds where the known key moved, but it cannot derive a genuinely new or obfuscated key.

An explicit key can be supplied through the vendored parser API, although the GUI does not currently expose that override.

## Requirements

- Grand Theft Auto IV.
- FFmpeg available through `PATH`.
- FusionFix for the recommended override-based replacement mode.

Python 3.12 is required only when running from source. The portable Windows build includes the Python runtime and application dependencies.

The application can offer to install FFmpeg when it is missing. Manual installation is also supported.

Episodes from Liberty City support has not been validated.

## Installation

### Portable Windows build

Download the `GTAIVModdingToolkit-windows-*.zip` artifact from the **Portable Windows Build** workflow, extract the complete directory, and run `GTAIVModdingToolkit.exe`. Keep the `_internal` directory beside the executable.

### Run from source

```bash
git clone https://github.com/Heidric/GTAIVModdingToolkit.git
cd GTAIVModdingToolkit
py -3.12 -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

Run the application:

```bash
.venv/Scripts/python.exe app.py
```

## Usage

### Startup settings

The start page reuses the last valid GTA IV directory and replacement method. When no saved installation is available and automatic detection is enabled, the toolkit checks `GTAIV_PATH`, Steam's registered installation and `libraryfolders.vdf`, and common Rockstar Games Launcher and Epic Games locations.

On Windows, discovery also checks a bounded list of common game folders on each fixed local drive, including `Games`, `GOG Games`, and `Rockstar Games`. It does not recursively scan entire disks.

Select **Detect** to run discovery manually and choose between multiple installations. A valid game directory must contain both `GTAIV.exe` and `pc/audio/sfx`.

Open **Settings & About** to change the saved installation, select the default replacement method, enable or disable automatic detection, view build metadata, or open the application log directory.

### Single-track replacement

1. Select the GTA IV installation directory.
2. Select FusionFix or direct replacement mode.
3. Select a radio station.
4. Select an existing track slot.
5. Select an MP3, WAV, or OGG replacement.
6. Wait for staging, conversion, byte verification, and commit to complete.
7. Test the modified station in game.

### Batch replacement

1. Open a radio station.
2. Select **Batch Replace**.
3. Add one or more audio files.
4. Review or change the target slot assigned to each file.
5. Confirm that every target slot is unique.
6. Select **Replace All**.
7. Wait for conversion, staging, byte verification, and commit to complete.
8. Test the modified station in game.

The batch page displays the number of replaceable slots, selected files, and remaining slots before processing starts.

### Radio-logo replacement and recovery

1. Open **Radio Logo Tools** from the station-selection page.
2. Select GTA IV, TLAD, or TBoGT as the texture target.
3. Select an existing station and an input image. Transparent PNG or WebP input is recommended.
4. Choose **Fit**, **Fill**, or **Stretch**, and adjust safe padding.
5. Review the selected/color and unselected/grayscale previews.
6. Select **Install Station Logo** and confirm the transactional installation.
7. To undo the latest logo operation, open the **Recovery** tab and select **Restore Previous Logo State**.

The image workflow changes existing `_col` and `_bw` payloads while preserving the original WTD resource layout. Generated package directories are temporary unless explicitly requested through the backend API.

After installation or recovery, returning to the station-selection page rebuilds its icons from the active GTA IV WTD files. The displayed icon therefore follows the current direct or FusionFix texture state instead of the bundled identification image.

Recovery operates on one complete backup batch. In direct mode it restores the newest timestamped WTD backups. In FusionFix mode it restores the newest override backups, or removes the first override batch so the game falls back to its original WTD files. The displaced active state is backed up, allowing the recovery operation itself to be reversed.

### Diagnostics and support bundles

The application writes rotating text logs under the current user's local application-data directory. On Windows the default location is:

```text
%LOCALAPPDATA%\GTAIVModdingToolkit\logs\
```

Select **Create Support Bundle** on the start page to export a ZIP containing:

- application version and build metadata;
- Windows and Python runtime details;
- FFmpeg and bundled-tool availability;
- presence, size, and modification time for relevant GTA IV paths;
- a limited tail of recent application logs.

The bundle does not include GTA IV executables, RPF/WTD archives, audio, replacement images, or other game-file contents. Known user-home, temporary, and selected GTA IV paths are replaced with placeholders. Review the ZIP before sharing it.

### WTD write safety

The production image workflow uses **surgical payload patching**. It preserves the original RSC5 header, virtual metadata, texture table, dimensions, formats, mip counts, and every physical byte outside the selected texture payloads. The **Check Readiness** action verifies Pillow, the texfury encoder, station source WTDs, the input image, temporary storage, and destination write access before installation.

Full WTD reconstruction through texfury dictionary saving or FusionFix ResourceBuilder remains available only for development diagnostics. These paths are not used by the GUI installation workflow and require explicit acknowledgement:

```bash
python -m core.radio_logo.texture_dictionary --help
python -m core.radio_logo.resource_builder --help
```

Pass `--experimental` to the selected command, pass `allow_experimental=True` through the Python API, or set `GTAIV_TOOLKIT_ENABLE_EXPERIMENTAL_WTD_REBUILD=1`. Experimental output must not be treated as production-safe merely because structural validation succeeds.

## Reverting changes

### FusionFix mode

Radio-logo changes can be reverted from **Radio Logo Tools → Recovery**. Manual removal is also possible by deleting the relevant `radio_hud*.wtd` files under the matching texture directory:

```text
<gtaiv>/update/pc/textures/
<gtaiv>/update/TLAD/pc/textures/
<gtaiv>/update/TBoGT/pc/textures/
```

To remove a station audio override, delete its RPF from:

```text
<gtaiv>/update/pc/audio/sfx/
```

Track durations are stored in:

```text
<gtaiv>/update/pc/audio/config/sounds.dat15
```

Deleting the overridden `sounds.dat15` reverts duration metadata for every station represented by that override file.

### Direct replacement mode

Use **Radio Logo Tools → Recovery** to restore the latest complete radio-logo backup batch. Audio and logo backups are also stored next to the modified files with timestamped names. If no usable backup remains, restore the original game files through the game platform's file-verification mechanism.

## Development and tests

Install the test dependencies:

```bash
.venv/Scripts/python.exe -m pip install -r requirements-test.txt
```

Run the regression suite:

```bash
.venv/Scripts/python.exe -m pytest -q
```

Compile-check the tested Python modules:

```bash
.venv/Scripts/python.exe -m compileall -q core ui vendor tests
```

Build the portable Windows directory locally:

```bash
.venv/Scripts/python.exe -m pip install -r requirements-build.txt
.venv/Scripts/python.exe -m PyInstaller --clean --noconfirm GTAIVModdingToolkit.spec
dist/GTAIVModdingToolkit/GTAIVModdingToolkit.exe --smoke-test
```

The portable workflow runs the regression suite, builds the one-directory application, smoke-tests the packaged executable, and publishes a ZIP artifact.

GitHub Actions runs the compile check and test suite on Windows with Python 3.12 for pushes and pull requests.

The synthetic tests do not require GTA IV files and cover:

- AES-key normalization and unknown-offset scanning;
- encrypted and unencrypted RPF3 TOCs;
- capacity calculation from the next entry or archive end;
- in-place replacement with smaller and exact-capacity payloads;
- oversized-entry relocation to aligned EOF;
- preservation of adjacent entries;
- TOC persistence after reopening an archive;
- extracted-byte verification;
- missing-path and invalid-offset failures;
- transactional single-track staging, verification, cancellation, and rollback;
- support-bundle path redaction, log collection, and archive contents;
- GTA IV installation discovery and preference persistence.

## Third-party components

### RAGE Audio Toolkit and GTA IV Audio Editor

Created by [AndrewMulti](https://github.com/AndrewMulti). The bundled command-line tools are used for extracting and rebuilding GTA IV audio assets and `sounds.dat15` metadata.

### BASS Audio Library

Developed by [Un4seen Developments](https://www.un4seen.com/). Runtime components used by the bundled audio tools include BASS, BASSmix, and BASSenc.

### pyrpfiv

The project vendors and modifies `pyrpfiv`, originally by gmroder, under its MIT license. See:

- `vendor/pyrpfiv/LICENSE`
- `vendor/pyrpfiv/THIRD_PARTY_NOTICES.md`

### GTA Forums community

The original radio-replacement workflow was informed by MeshugaPalejo's GTA Forums guide to replacing songs on existing radio stations.

## Legal and safety notice

Use the toolkit only with game files you are legally entitled to modify. The repository does not include GTA IV game archives or executable files.

Radio-station logos included under `assets/` are used for identification in this non-commercial fan-made project. The logos were sourced from *HQ Radio Icons 1.2* by Sborges98. Rights to GTA IV and its original assets belong to their respective owners.

## License

The toolkit is licensed under the MIT License. See [LICENSE](LICENSE).
