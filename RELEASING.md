# Releasing GTA IV Modding Toolkit

The portable Windows workflow creates ordinary snapshot artifacts for `main` and publishes a GitHub Release for version tags.

## Release procedure

1. Update `APP_VERSION` in `core/build_info.py` and every Windows version field in `packaging/windows_version_info.txt`.
2. Update `README.md` and `RELEASE_NOTES.md` so the documented feature scope and limitations match the release.
3. Run the regression suite and build the portable Windows package.
4. Run packaged `--smoke-test` and `--doctor --packaged-only`.
5. Perform a real-game smoke test covering RPF/IMG open and filtering, generic entry replacement, WTD and WDR inspection/export, individual and queued texture replacement, archive reopen, LOD behavior, and retained-backup discovery.
6. Commit the release preparation using a Conventional Commit message.
7. Create an annotated tag matching `v<APP_VERSION>` exactly.
8. Push the commit and tag.

Example for version `0.16.0`:

```bash
git tag -a v0.16.0 -m "GTA IV Modding Toolkit 0.16.0"
git push origin main v0.16.0
```

The workflow rejects a tag whose name does not match `core.build_info.APP_VERSION`.

## Published files

A tagged build publishes:

- `GTAIVModdingToolkit-<version>-windows-x64.zip`
- the matching `.zip.sha256` checksum file
- generated GitHub release notes, reviewed against `RELEASE_NOTES.md`

The application embeds the commit SHA, UTC build time, and build channel. These values are available through:

```bash
GTAIVModdingToolkit.exe --version
```

## Checksum verification

PowerShell:

```powershell
Get-FileHash -Algorithm SHA256 .\GTAIVModdingToolkit-<version>-windows-x64.zip
```

Compare the result with the hexadecimal value in the published `.sha256` file.
