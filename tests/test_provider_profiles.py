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
        stdin=StringIO("openai-api\n1\n\n\n2\n\n"),
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
        stdin=StringIO("anthropic\nclaude-opus-4-8\n\n\n\n"),
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
        stdin=StringIO("anthropic\nclaude-sonnet-4-6\n\n\nn\n1\nsk-ant-new\n"),
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
        stdin=StringIO("\n\n\n\n\n"),
        stdout=StringIO(),
        existing=existing_config(),
    )

    assert config.active_provider == "anthropic"
    assert config.providers["anthropic"].api_key_env == "ANTHROPIC_API_KEY"


def test_setup_allows_split_providers_for_main_and_sub_lm() -> None:
    from fractal.onboarding import prompt_for_config

    config = prompt_for_config(
        # provider, model, sub-provider, sub-model (sub provider default),
        # main auth (env + default var), sub auth (env + default var)
        stdin=StringIO("anthropic\nclaude-fable-5\ngroq\n\n2\n\n2\n\n"),
        stdout=StringIO(),
    )

    assert config.active_provider == "anthropic"
    assert config.active_model == "claude-fable-5"
    assert config.active_sub_provider == "groq"
    assert config.active_sub_model == "openai/gpt-oss-120b"
    assert config.providers["anthropic"].api_key_env == "ANTHROPIC_API_KEY"
    assert config.providers["groq"].api_key_env == "GROQ_API_KEY"


def test_setup_same_sub_provider_choice_normalizes_to_follows_main() -> None:
    from fractal.onboarding import prompt_for_config

    config = prompt_for_config(
        # Picking the main provider again as sub-provider must not record a
        # redundant active_sub_provider.
        stdin=StringIO("anthropic\nclaude-fable-5\nanthropic\n\n2\n\n"),
        stdout=StringIO(),
    )

    assert config.active_provider == "anthropic"
    assert config.active_sub_provider is None
    assert config.active_sub_model is None


def test_sub_selection_from_config_resolves_split_provider() -> None:
    from fractal.config import FractalConfig, ProviderConfig
    from fractal.runtime_lms import sub_selection_from_config

    providers = {
        "anthropic": ProviderConfig(auth_source="env", api_key_env="ANTHROPIC_API_KEY"),
        "groq": ProviderConfig(auth_source="env", api_key_env="GROQ_API_KEY"),
    }

    split = FractalConfig(
        active_provider="anthropic",
        active_model="claude-fable-5",
        active_sub_provider="groq",
        active_sub_model="qwen/qwen3-32b",
        providers=providers,
    )
    selection = sub_selection_from_config(split)
    assert selection is not None
    assert selection.provider == "groq"
    assert selection.model == "qwen/qwen3-32b"
    assert selection.api_key_env == "GROQ_API_KEY"

    same_provider = FractalConfig(
        active_provider="anthropic",
        active_model="claude-fable-5",
        active_sub_model="claude-haiku-4-5",
        providers=providers,
    )
    selection = sub_selection_from_config(same_provider)
    assert selection is not None
    assert selection.provider == "anthropic"
    assert selection.model == "claude-haiku-4-5"

    follows_main = FractalConfig(
        active_provider="anthropic",
        active_model="claude-fable-5",
        providers=providers,
    )
    assert sub_selection_from_config(follows_main) is None


def test_config_rejects_unknown_sub_provider() -> None:
    import pytest

    from fractal.config import FractalConfig, ProviderConfig

    with pytest.raises(ValueError, match="active_sub_provider"):
        FractalConfig(
            active_provider="anthropic",
            active_model="claude-fable-5",
            active_sub_provider="groq",
            providers={
                "anthropic": ProviderConfig(
                    auth_source="env", api_key_env="ANTHROPIC_API_KEY"
                )
            },
        )
