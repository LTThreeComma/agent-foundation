from converge_foundation_service.settings import ServiceRole, ServiceSettings


def test_settings_use_all_in_one_role_by_default() -> None:
    settings = ServiceSettings()

    assert settings.role is ServiceRole.all
    assert settings.port == 8000
    assert "foundation:foundation" not in repr(settings)
