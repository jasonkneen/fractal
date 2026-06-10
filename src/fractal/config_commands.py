from __future__ import annotations

import sys
from typing import Any, TextIO

from .onboarding import SetupInputError, prompt_for_config
from .runtime_lms import selection_from_config


def run_config_command(
    args: Any,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    offline = bool(getattr(args, "offline", False))
    workspace = getattr(args, "workspace", None)
    if args.config_command == "show":
        return config_show(stdout=stdout, stderr=stderr, workspace=workspace)
    if args.config_command == "status":
        return config_status(
            stdout=stdout,
            stderr=stderr,
            offline=offline,
            workspace=workspace,
        )
    if args.config_command == "setup":
        return config_setup(stdin=stdin, stdout=stdout, stderr=stderr, offline=offline)
    if args.config_command == "get":
        return config_get(args.key, stdout=stdout, stderr=stderr, workspace=workspace)
    if args.config_command == "set":
        return config_set(
            args.key,
            args.value,
            stdout=stdout,
            stderr=stderr,
            project=bool(getattr(args, "project", False)),
            workspace=workspace,
        )
    if args.config_command == "reset":
        return config_reset(
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            credentials=bool(getattr(args, "credentials", False)),
            assume_yes=bool(getattr(args, "yes", False)),
        )
    if args.config_command == "unset":
        return config_unset(
            args.key,
            stdout=stdout,
            stderr=stderr,
            project=bool(getattr(args, "project", False)),
            workspace=workspace,
        )
    print(f"fractal config: unknown command {args.config_command!r}", file=stderr)
    return 1


def config_show(
    *,
    stdout: TextIO,
    stderr: TextIO,
    workspace: Any | None = None,
) -> int:
    from .config import FractalConfigError, load_layered_config, render_config

    try:
        result = load_layered_config(workspace=workspace)
    except FractalConfigError as exc:
        print(f"fractal config: {exc}", file=stderr)
        return 1
    if result.config is None:
        print(f"fractal config: no config found at {result.path}", file=stderr)
        print("Run `fractal config setup`.", file=stderr)
        return 1
    print(render_config(result.config, path=result.path), file=stdout)
    _print_layer_notes(result, stdout=stdout)
    return 0


def _print_layer_notes(result: Any, *, stdout: TextIO) -> None:
    if result.project_path is not None:
        print(f"project overrides: {result.project_path}", file=stdout)
    if result.env_overrides:
        print("env overrides: " + ", ".join(result.env_overrides), file=stdout)


def config_status(
    *,
    stdout: TextIO,
    stderr: TextIO,
    offline: bool = False,
    workspace: Any | None = None,
) -> int:
    from .config import FractalConfigError, load_layered_config, render_config
    from .connectivity import ProviderConnectivityError, check_provider_connectivity
    from .providers import ProviderError, check_provider_readiness

    try:
        result = load_layered_config(workspace=workspace)
    except FractalConfigError as exc:
        print("Fractal config status: invalid", file=stdout)
        print(f"fractal config: {exc}", file=stderr)
        print("Run `fractal config setup` after fixing the config.", file=stderr)
        return 1
    if result.config is None:
        print("Fractal config status: not configured", file=stdout)
        print(f"path: {result.path}", file=stdout)
        print("Run `fractal config setup`.", file=stdout)
        return 1

    selection = selection_from_config(result.config, path=result.path)
    _check_model_against_catalog(
        result.config.active_provider,
        result.config.active_model,
        label="active_model",
        stderr=stderr,
    )
    if result.config.active_sub_model is not None:
        _check_model_against_catalog(
            result.config.active_provider,
            result.config.active_sub_model,
            label="active_sub_model",
            stderr=stderr,
        )
    try:
        check_provider_readiness(selection)
    except ProviderError as exc:
        print("Fractal config status: invalid", file=stdout)
        print(render_config(result.config, path=result.path), file=stdout)
        print(f"auth/provider check failed: {exc}", file=stderr)
        print(
            "Run `fractal config setup` or fix the configured auth source.",
            file=stderr,
        )
        return 1

    connectivity_note = "connectivity: skipped (--offline)"
    if not offline:
        try:
            checked = check_provider_connectivity(selection)
        except ProviderConnectivityError as exc:
            print("Fractal config status: unreachable", file=stdout)
            print(render_config(result.config, path=result.path), file=stdout)
            print(f"connectivity check failed: {exc}", file=stderr)
            print(
                "Fix the credential or network, or re-run with `--offline`.",
                file=stderr,
            )
            return 1
        connectivity_note = (
            "connectivity: verified"
            if checked
            else "connectivity: not checked for this provider"
        )

    print("Fractal config status: ok", file=stdout)
    print(render_config(result.config, path=result.path), file=stdout)
    _print_layer_notes(result, stdout=stdout)
    print(connectivity_note, file=stdout)
    return 0


def config_setup(
    *,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    offline: bool = False,
) -> int:
    from .config import FractalConfigError, write_config
    from .connectivity import ProviderConnectivityError, check_provider_connectivity
    from .providers import (
        MissingProviderCredentialError,
        ProviderError,
        check_provider_readiness,
    )

    # A missing credential should not discard the user's setup answers: the
    # config holds only non-secret references, so write it and tell the user
    # exactly what to provide. Any other failure means the setup itself is
    # wrong and nothing should be written.
    credential_warning: str | None = None
    try:
        config = prompt_for_config(
            stdin=stdin,
            stdout=stdout,
            existing=_existing_config_for_setup(),
        )
        selection = selection_from_config(config)
        try:
            check_provider_readiness(selection)
        except MissingProviderCredentialError as exc:
            credential_warning = str(exc)
        path = write_config(config)
    except (FractalConfigError, ProviderError, SetupInputError, ValueError) as exc:
        print(f"fractal config setup: {exc}", file=stderr)
        print(
            "No config was written. Fix the issue, then run "
            "`fractal config setup` again.",
            file=stderr,
        )
        return 1

    print(f"Fractal config written to {path}", file=stdout)
    if credential_warning is not None:
        print(f"warning: {credential_warning}", file=stderr)
        print(
            "Provide the credential, then run `fractal config status` to verify.",
            file=stderr,
        )
        return 0

    # Verify the credential actually works, not just that it exists. Network
    # problems should not undo a finished setup, so failures only warn.
    if not offline:
        try:
            if check_provider_connectivity(selection):
                print("Provider connectivity verified.", file=stdout)
        except ProviderConnectivityError as exc:
            print(f"warning: {exc}", file=stderr)
            print(
                "The config was written. Fix the credential or network, then "
                "run `fractal config status` to verify.",
                file=stderr,
            )
    return 0


def _existing_config_for_setup() -> Any | None:
    """Best-effort load of the current global config so setup can merge.

    A corrupt config means setup is repairing it, so merging is impossible
    and starting fresh is the right call.
    """
    from .config import FractalConfigError, load_config

    try:
        return load_config().config
    except FractalConfigError:
        return None


def config_reset(
    *,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    credentials: bool = False,
    assume_yes: bool = False,
) -> int:
    from .config import FractalConfigError, default_config_path
    from .credentials import (
        default_credentials_path,
        delete_credential,
        load_stored_credentials,
    )

    config_path = default_config_path()
    credentials_path = default_credentials_path()
    targets = [path for path in (config_path,) if path.exists()]
    if credentials and credentials_path.exists():
        targets.append(credentials_path)

    if not targets:
        print("fractal config: nothing to reset", file=stdout)
        return 0

    if not assume_yes:
        print("This will delete:", file=stdout)
        for path in targets:
            note = " (stored API keys)" if path == credentials_path else ""
            print(f"  {path}{note}", file=stdout)
        print("Continue? [y/N]: ", file=stdout, end="", flush=True)
        answer = stdin.readline().strip().lower()
        if answer not in {"y", "yes"}:
            print("fractal config: reset aborted", file=stdout)
            return 1

    try:
        if config_path.exists():
            config_path.unlink()
            print(f"deleted {config_path}", file=stdout)
        if credentials and credentials_path.exists():
            # Clear each stored key before removing the file so a corrupt
            # credentials file still gets deleted instead of blocking reset.
            try:
                for provider_id in load_stored_credentials(credentials_path):
                    delete_credential(provider_id, credentials_path)
            except FractalConfigError:
                pass
            credentials_path.unlink()
            print(f"deleted {credentials_path}", file=stdout)
    except OSError as exc:
        print(f"fractal config: reset failed: {exc}", file=stderr)
        return 1

    print("Run `fractal config setup` to configure Fractal again.", file=stdout)
    return 0


def config_get(
    key: str,
    *,
    stdout: TextIO,
    stderr: TextIO,
    workspace: Any | None = None,
) -> int:
    from .config import FractalConfigError, load_layered_config

    try:
        result = load_layered_config(workspace=workspace)
    except FractalConfigError as exc:
        print(f"fractal config: {exc}", file=stderr)
        return 1
    if result.config is None:
        print("fractal config: not configured; run `fractal config setup`.", file=stderr)
        return 1

    data = result.config.model_dump(mode="python", exclude_none=True)
    found, value = _walk_config_path(data, key)
    if not found:
        print(f"fractal config: {key} is not set", file=stderr)
        return 1
    print(_format_config_value(value), file=stdout)
    return 0


def config_set(
    key: str,
    raw_value: str,
    *,
    stdout: TextIO,
    stderr: TextIO,
    project: bool = False,
    workspace: Any | None = None,
) -> int:
    from pathlib import Path

    from .config import (
        FractalConfig,
        FractalConfigError,
        ProjectFractalConfig,
        load_config,
        load_project_config,
        write_config,
        write_project_config,
    )

    value = _parse_config_value(raw_value)
    try:
        if project:
            target_workspace = Path(workspace) if workspace is not None else Path.cwd()
            current = load_project_config(target_workspace) or ProjectFractalConfig()
            data = current.model_dump(mode="python", exclude_none=True)
            _set_config_path(data, key, value)
            updated = ProjectFractalConfig.model_validate(data)
            if key in {"active_model", "active_sub_model"}:
                provider_id = updated.active_provider
                if provider_id is None:
                    try:
                        global_config = load_config().config
                    except FractalConfigError:
                        global_config = None
                    if global_config is not None:
                        provider_id = global_config.active_provider
                if provider_id is not None and not _check_model_against_catalog(
                    provider_id, str(value), label=key, stderr=stderr
                ):
                    return 1
            path = write_project_config(updated, target_workspace)
        else:
            result = load_config()
            if result.config is None:
                print(
                    "fractal config: not configured; run `fractal config setup` "
                    "first or use `--project`.",
                    file=stderr,
                )
                return 1
            data = result.config.model_dump(mode="python", exclude_none=True)
            _set_config_path(data, key, value)
            updated = FractalConfig.model_validate(data)
            if key in {"active_model", "active_sub_model"} and not (
                _check_model_against_catalog(
                    updated.active_provider, str(value), label=key, stderr=stderr
                )
            ):
                return 1
            path = write_config(updated, path=result.path)
    except FractalConfigError as exc:
        print(f"fractal config: {exc}", file=stderr)
        return 1
    except ValueError as exc:
        print(f"fractal config: cannot set {key}: {exc}", file=stderr)
        return 1

    print(f"set {key} = {_format_config_value(value)} in {path}", file=stdout)
    return 0


def config_unset(
    key: str,
    *,
    stdout: TextIO,
    stderr: TextIO,
    project: bool = False,
    workspace: Any | None = None,
) -> int:
    from pathlib import Path

    from .config import (
        FractalConfig,
        FractalConfigError,
        ProjectFractalConfig,
        load_config,
        load_project_config,
        write_config,
        write_project_config,
    )

    try:
        if project:
            target_workspace = Path(workspace) if workspace is not None else Path.cwd()
            current = load_project_config(target_workspace)
            if current is None:
                print("fractal config: no project config found", file=stderr)
                return 1
            data = current.model_dump(mode="python", exclude_none=True)
            if not _unset_config_path(data, key):
                print(f"fractal config: {key} is not set", file=stderr)
                return 1
            updated = ProjectFractalConfig.model_validate(data)
            path = write_project_config(updated, target_workspace)
        else:
            result = load_config()
            if result.config is None:
                print("fractal config: not configured", file=stderr)
                return 1
            data = result.config.model_dump(mode="python", exclude_none=True)
            if not _unset_config_path(data, key):
                print(f"fractal config: {key} is not set", file=stderr)
                return 1
            updated = FractalConfig.model_validate(data)
            path = write_config(updated, path=result.path)
    except FractalConfigError as exc:
        print(f"fractal config: {exc}", file=stderr)
        return 1
    except ValueError as exc:
        print(f"fractal config: cannot unset {key}: {exc}", file=stderr)
        return 1

    print(f"unset {key} in {path}", file=stdout)
    return 0


def _check_model_against_catalog(
    provider_id: str,
    model: str,
    *,
    label: str,
    stderr: TextIO,
) -> bool:
    """Validate a model id against the provider's catalog.

    Returns False only when the provider restricts model ids and this one is
    not allowed. Unknown models on custom-friendly providers (and unknown
    providers) only warn, since the catalog is a suggestion, not a contract.
    """
    from .providers import UnknownProviderError, get_provider

    try:
        provider = get_provider(provider_id)
    except UnknownProviderError:
        return True
    known = {
        provider.default_model,
        *provider.model_options,
        *provider.restricted_models,
    }
    if model in known:
        return True
    if not provider.allows_custom_model:
        allowed = ", ".join(provider.restricted_models)
        print(
            f"fractal config: {model!r} is not supported by "
            f"{provider_id} (supported: {allowed})",
            file=stderr,
        )
        return False
    print(
        f"warning: {model!r} ({label}) is not in the known "
        f"{provider_id} catalog; make sure the provider supports it",
        file=stderr,
    )
    return True


def _parse_config_value(raw: str) -> Any:
    import tomllib

    try:
        return tomllib.loads(f"value = {raw}")["value"]
    except tomllib.TOMLDecodeError:
        return raw


def _format_config_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        import tomli_w

        return tomli_w.dumps(value).strip()
    return str(value)


def _walk_config_path(data: Any, key: str) -> tuple[bool, Any]:
    node = data
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _set_config_path(data: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    node: Any = data
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    if not isinstance(node, dict):
        raise ValueError(f"{key} does not address a config table")
    node[parts[-1]] = value


def _unset_config_path(data: dict[str, Any], key: str) -> bool:
    parts = key.split(".")
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    if not isinstance(node, dict) or parts[-1] not in node:
        return False
    del node[parts[-1]]
    return True
