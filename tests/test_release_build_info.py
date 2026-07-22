import pytest

import build_info


def test_release_tag_tracks_application_version():
    assert build_info.release_tag() == f"v{build_info.APP_VERSION}"


def test_validate_release_ref_accepts_matching_tag():
    build_info.validate_release_ref(build_info.release_tag())


def test_validate_release_ref_rejects_mismatched_tag():
    with pytest.raises(ValueError, match="does not match application version"):
        build_info.validate_release_ref("v999.0.0")


def test_build_summary_contains_distribution_metadata():
    summary = build_info.build_summary()

    assert build_info.application_title() in summary
    assert "Channel:" in summary
    assert "Commit:" in summary
