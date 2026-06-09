from __future__ import annotations

import tomllib
from io import StringIO
from pathlib import Path

import pytest


def existing_config():
    from fractal.config import FractalConfig, ProviderConfig

    return FractalConfig(
        active_provider="anthropic",
        active_model="claude-sonnet-4-6",
        active_sub_model="claude-haiku-4-5",
        providers={
            "anthropic": ProviderConfig(
                auth_source="env",
                api_key_env="ANTHROPIC_API_KEY",
            )
        },
        defaults={"max_iterations": 12},
    )


def test_setup_merges_new_provider_into_existing_config() -> None:
    from fractal.onboarding import prompt_for_config

    config = prompt_for_config(
        stdin=StringIO("openai-api\n1\n2\n\n"),
        stdout=StringIO(),
        existing=existing_config(),
    )

    assert config.active_provider == "openai-api"
    assert config.active_model == "gpt-5.5"
    # The previously configured provider keeps its profile.
    assert config.providers["anthropic"].api_key_env == "ANTHROPIC_API_KEY"
    assert config.providers["openai-api"].api_key_env == "OPENAI_API_KEY"
    # Run defaults survive; the sub-model is dropped with the provider switch.
    assert config.defaults.max_iterations == 12
    assert config.active_sub_model is None


def test_setup_reuses_saved_auth_for_configured_provider() -> None:
    from fractal.onboarding import prompt_for_config

    stdout = StringIO()
    config = prompt_for_config(
        stdin=StringIO("anthropic\nclaude-opus-4-8\n\n"),
        stdout=stdout,
        existing=existing_config(),
    )

    assert config.active_provider == "anthropic"
    assert config.active_model == "claude-opus-4-8"
    assert config.providers["anthropic"].api_key_env == "ANTHROPIC_API_KEY"
    # Same provider: the sub-model stays valid and is preserved.
    assert config.active_sub_model == "claude-haiku-4-5"
    assert "Use saved auth settings" in stdout.getvalue()
    assert "(configured)" in stdout.getvalue()


def test_setup_can_reconfigure_auth_for_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fractal.onboarding import prompt_for_config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = prompt_for_config(
        stdin=StringIO("anthropic\nclaude-sonnet-4-6\nn\n1\nsk-ant-new\n"),
        stdout=StringIO(),
        existing=existing_config(),
    )

    assert config.providers["anthropic"].auth_source == "stored"
    assert config.providers["anthropic"].api_key_env is None
    credentials = tomllib.loads(
        (tmp_path / "fractal" / "credentials.toml").read_text(encoding="utf-8")
    )
    assert credentials["api_keys"]["anthropic"] == "sk-ant-new"


def test_setup_default_provider_is_the_active_one() -> None:
    from fractal.onboarding import prompt_for_config

    # Blank answers accept the defaults: active provider, default model,
    # saved auth.
    config = prompt_for_config(
        stdin=StringIO("\n\n\n"),
        stdout=StringIO(),
        existing=existing_config(),
    )

    assert config.active_provider == "anthropic"
    assert config.providers["anthropic"].api_key_env == "ANTHROPIC_API_KEY"
