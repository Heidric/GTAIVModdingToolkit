from types import SimpleNamespace

from core.system_check import CheckStatus, format_system_check_report, run_system_check


def make_resource_root(tmp_path):
    root = tmp_path / "resources"
    (root / "assets").mkdir(parents=True)
    (root / "tools").mkdir()
    (root / "tools" / "ivam.exe").write_bytes(b"tool")
    (root / "tools" / "IVAudioConv.exe").write_bytes(b"tool")
    return root


def make_game(tmp_path, *, fusionfix=True):
    root = tmp_path / "GTAIV"
    (root / "pc" / "audio" / "sfx").mkdir(parents=True)
    (root / "pc" / "audio" / "config").mkdir(parents=True)
    (root / "pc" / "textures").mkdir(parents=True)
    (root / "GTAIV.exe").write_bytes(b"game")
    (root / "pc" / "audio" / "sfx" / "radio_test.rpf").write_bytes(b"rpf")
    (root / "pc" / "audio" / "config" / "sounds.dat15").write_bytes(b"dat")
    (root / "pc" / "textures" / "radio_hud.wtd").write_bytes(b"wtd")
    if fusionfix:
        (root / "plugins").mkdir()
        (root / "plugins" / "FusionFix.asi").write_bytes(b"plugin")
    return root


def dependency_loader(_name):
    return object()


def successful_runner(*_args, **_kwargs):
    return SimpleNamespace(returncode=0, stdout="ffmpeg version test\n", stderr="")


def test_packaged_only_check_passes_without_ffmpeg(tmp_path):
    report = run_system_check(
        packaged_only=True,
        resource_root=make_resource_root(tmp_path),
        which=lambda _name: None,
        dependency_loader=dependency_loader,
    )

    assert report.exit_code == 0
    assert any(item.status is CheckStatus.WARNING for item in report.items)
    assert any(item.key == "game.skipped" for item in report.items)


def test_missing_bundled_tool_is_blocking(tmp_path):
    resources = make_resource_root(tmp_path)
    (resources / "tools" / "ivam.exe").unlink()

    report = run_system_check(
        packaged_only=True,
        resource_root=resources,
        which=lambda _name: None,
        dependency_loader=dependency_loader,
    )

    assert report.exit_code == 1
    failed = {item.key for item in report.items if item.status is CheckStatus.FAIL}
    assert "resource.ivam" in failed


def test_valid_fusionfix_installation_passes_blocking_checks(tmp_path):
    report = run_system_check(
        make_game(tmp_path),
        use_direct=False,
        resource_root=make_resource_root(tmp_path),
        which=lambda name: f"C:/tools/{name}.exe",
        runner=successful_runner,
        dependency_loader=dependency_loader,
    )

    assert report.exit_code == 0
    assert not report.has_failures
    assert any(item.key == "mode.fusionfix" for item in report.items)


def test_missing_fusionfix_is_blocking_in_safe_mode(tmp_path):
    report = run_system_check(
        make_game(tmp_path, fusionfix=False),
        use_direct=False,
        resource_root=make_resource_root(tmp_path),
        which=lambda _name: None,
        dependency_loader=dependency_loader,
    )

    assert report.exit_code == 1
    item = next(item for item in report.items if item.key == "mode.fusionfix")
    assert item.status is CheckStatus.FAIL
    assert item.blocking is True


def test_direct_mode_warns_without_blocking(tmp_path):
    report = run_system_check(
        make_game(tmp_path, fusionfix=False),
        use_direct=True,
        resource_root=make_resource_root(tmp_path),
        which=lambda _name: None,
        dependency_loader=dependency_loader,
    )

    assert report.exit_code == 0
    item = next(item for item in report.items if item.key == "mode.direct")
    assert item.status is CheckStatus.WARNING


def test_text_report_contains_statuses_and_summary(tmp_path):
    report = run_system_check(
        packaged_only=True,
        resource_root=make_resource_root(tmp_path),
        which=lambda _name: None,
        dependency_loader=dependency_loader,
    )

    text = format_system_check_report(report)

    assert "[PASS] Bundled assets" in text
    assert "[WARN] FFmpeg" in text
    assert "Summary:" in text
