from build_info import APP_NAME, APP_VERSION, application_title


def test_application_title_contains_name_and_version():
    assert APP_NAME in application_title()
    assert APP_VERSION in application_title()
