# Releasing GTA IV Modding Toolkit

The portable Windows workflow creates ordinary snapshot artifacts for `main` and publishes a GitHub Release for version tags.

## Release procedure

1. Update `APP_VERSION` in `build_info.py` and the Windows file version in `packaging/windows_version_info.txt`.
2. Run the regression suite and packaged smoke test.
3. Commit the version change using a Conventional Commit message.
4. Create an annotated tag matching `v<APP_VERSION>` exactly.
5. Push the commit and tag.

Example for version `0.15.0`:

```bash
git tag -a v0.15.0 -m "GTA IV Modding Toolkit 0.15.0"
git push origin main v0.15.0
```

The workflow rejects a tag whose name does not match `build_info.APP_VERSION`.

## Published files

A tagged build publishes:

- `GTAIVModdingToolkit-<version>-windows-x64.zip`
- the matching `.zip.sha256` checksum file
- generated GitHub release notes

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
