from __future__ import annotations

import os

if os.environ.get("NO_RICH"):
    import click as click
else:
    import rich_click as click

from lamindb_setup.errors import DevDirNonEmpty, NoDevDirConfigured


def _set_worktree(settings_, value: bool) -> None:
    try:
        settings_.worktree = value
    except (NoDevDirConfigured, DevDirNonEmpty) as error:
        raise click.ClickException(str(error)) from error


@click.group(invoke_without_command=True)
@click.pass_context
def settings(ctx):
    """Manage development, cache, modules, branch, space, mount, and run settings.

    Get or set the following settings:

    - `dev-dir` → development directory {attr}`~lamindb.setup.core.SetupSettings.dev_dir`
    - `cache-dir` → cache directory {attr}`~lamindb.setup.core.SetupSettings.cache_dir`
    - `modules` → environment schema modules {attr}`~lamindb.setup.core.SetupSettings.modules`
    - `branch` → current {attr}`~lamindb.setup.core.SetupSettings.branch`
    - `space` → current {attr}`~lamindb.setup.core.SetupSettings.space`
    - `worktree` → toggle {attr}`~lamindb.setup.core.SetupSettings.worktree` mode (dev-dir is a worktree parent where each child directory maps on a branch)
    - `mount` → read-only mounts of storage locations, used by `lamin run` to read inputs in place
    - `run-where` → default place for `lamin run` (overridden by `$LAMIN_RUN_WHERE` and `--where`)
    - `run-recipe` → inspect/remove recipes for ambiguous `lamin run` targets (created via `lamin run --new-recipe`)

    You can display your current settings by running: `lamin info`

    Examples:

    ```
    # dev-dir
    lamin settings dev-dir get
    lamin settings dev-dir set .  # set to current directory
    lamin settings dev-dir set ~/my-project
    lamin settings dev-dir unset
    # cache-dir
    lamin settings cache-dir get
    lamin settings cache-dir set /path/to/cache
    lamin settings cache-dir clear
    # modules
    lamin settings modules get
    lamin settings modules set bionty,pertdb
    lamin settings modules unset
    # branch
    lamin settings branch get
    lamin settings branch set main
    # space
    lamin settings space get
    lamin settings space set all
    # worktree
    lamin settings worktree get
    lamin settings worktree set true
    lamin settings worktree unset
    # mount
    lamin settings mount storage ./mnt
    lamin settings mount path lamin://acme/data/artifact/key/my_file.parquet
    lamin settings mount unset ./mnt
    # run-where
    lamin settings run-where get
    lamin settings run-where set modal
    lamin settings run-where unset
    # run-recipe
    lamin settings run-recipe list
    lamin settings run-recipe show mytool
    lamin settings run-recipe remove mytool
    ```

    → Python/R alternative: {attr}`~lamindb.setup.core.SetupSettings.dev_dir`, {attr}`~lamindb.setup.core.SetupSettings.cache_dir`, {attr}`~lamindb.setup.core.SetupSettings.modules`, {attr}`~lamindb.setup.core.SetupSettings.branch`, and {attr}`~lamindb.setup.core.SetupSettings.space`
    """
    if ctx.invoked_subcommand is None:
        from lamindb_setup import settings as settings_

        click.echo("Configure: see `lamin settings --help`")
        click.echo(settings_)


# -----------------------------------------------------------------------------
# dev-dir group (pattern: lamin settings dev-dir get/set)
# -----------------------------------------------------------------------------


@click.group("dev-dir")
def dev_dir_group():
    """Get or set the development directory."""


@dev_dir_group.command("get")
def dev_dir_get():
    """Show the current development directory."""
    from lamindb_setup import settings as settings_

    value = settings_.dev_dir
    click.echo(value if value is not None else "None")


@dev_dir_group.command("set")
@click.argument("value", type=str)
def dev_dir_set(value: str):
    """Set the development directory."""
    from lamindb_setup import settings as settings_

    if value.lower() == "none":
        value = None  # type: ignore[assignment]
    settings_.dev_dir = value


@dev_dir_group.command("unset")
def dev_dir_unset():
    """Unset the development directory."""
    from lamindb_setup import settings as settings_

    settings_.dev_dir = None


settings.add_command(dev_dir_group)


# -----------------------------------------------------------------------------
# worktree group (pattern: lamin settings worktree get/set)
# -----------------------------------------------------------------------------


@click.group("worktree")
def worktree_group():
    """Get or set whether dev-dir is interpreted as a worktree parent."""


@worktree_group.command("get")
def worktree_get():
    """Show whether worktree mode is enabled."""
    from lamindb_setup import settings as settings_

    click.echo("true" if settings_.worktree else "false")


@worktree_group.command("set")
@click.argument("value", type=str)
def worktree_set(value: str):
    """Enable or disable worktree mode."""
    from lamindb_setup import settings as settings_

    value_normalized = value.strip().lower()
    if value_normalized in {"1", "true", "yes"}:
        _set_worktree(settings_, True)
        return
    if value_normalized in {"0", "false", "no"}:
        _set_worktree(settings_, False)
        return
    raise click.ClickException("Invalid value for worktree. Pass one of: true, false.")


@worktree_group.command("unset")
def worktree_unset():
    """Unset worktree mode (equivalent to false)."""
    from lamindb_setup import settings as settings_

    _set_worktree(settings_, False)


settings.add_command(worktree_group)


# -----------------------------------------------------------------------------
# run-where group (pattern: lamin settings run-where get/set)
# -----------------------------------------------------------------------------


@click.group("run-where")
def run_where_group():
    """Get or set where `lamin run` runs by default."""


@run_where_group.command("get")
def run_where_get():
    """Show the default place to run and where that default comes from."""
    from lamin_cli._run import RunError, resolve_where

    try:
        where, source = resolve_where(None)
    except RunError as error:
        raise click.ClickException(str(error)) from None
    click.echo(f"{where} (from {source})")


@run_where_group.command("set")
@click.argument("value", type=str)
def run_where_set(value: str):
    """Set the default place to run, e.g. `local` or `modal`."""
    from lamin_cli._run import RunError, write_where_setting

    try:
        write_where_setting(value)
    except RunError as error:
        raise click.ClickException(str(error)) from None


@run_where_group.command("unset")
def run_where_unset():
    """Unset the default place to run, falling back to `local`."""
    from lamin_cli._run import write_where_setting

    write_where_setting(None)


settings.add_command(run_where_group)


# run-recipe group: recipes are created via `lamin run --new-recipe`, this
# group is just for inspecting/removing what's already there
# -----------------------------------------------------------------------------


@click.group("run-recipe")
def run_recipe_group():
    """Inspect or remove recipes for ambiguous `lamin run` targets.

    Recipes are created interactively via `lamin run --new-recipe`, not here.
    """


@run_recipe_group.command("list")
@click.option(
    "--recipe-file",
    type=str,
    default=None,
    help="Defaults to $LAMIN_RUN_RECIPES, then a local per-machine file.",
)
def run_recipe_list(recipe_file: str | None):
    """List recipes, and where they're stored."""
    from lamin_cli._recipes import load_recipes, recipes_path

    path = recipes_path(recipe_file)
    recipes = load_recipes(path)
    click.echo(f"{path}:")
    if not recipes:
        click.echo("  (no recipes yet)")
        return
    for recipe in recipes:
        click.echo(f"  {recipe.target} {recipe.required_tokens}")


@run_recipe_group.command("show")
@click.argument("target", type=str)
@click.option(
    "--recipe-file",
    type=str,
    default=None,
    help="Defaults to $LAMIN_RUN_RECIPES, then a local per-machine file.",
)
def run_recipe_show(target: str, recipe_file: str | None):
    """Show every recipe defined for TARGET."""
    import json as _json

    from lamin_cli._recipes import load_recipes, recipes_path

    recipes = [r for r in load_recipes(recipes_path(recipe_file)) if r.target == target]
    if not recipes:
        raise click.ClickException(f"No recipe for {target!r}.")
    for recipe in recipes:
        click.echo(_json.dumps(recipe.to_dict(), indent=2))


@run_recipe_group.command("remove")
@click.argument("target", type=str)
@click.option(
    "--required-token",
    "required_tokens",
    multiple=True,
    type=str,
    help="The recipe's required_tokens, to pick one if TARGET has more than one shape. Repeatable, in order.",
)
@click.option(
    "--recipe-file",
    type=str,
    default=None,
    help="Defaults to $LAMIN_RUN_RECIPES, then a local per-machine file.",
)
def run_recipe_remove(
    target: str, required_tokens: tuple[str, ...], recipe_file: str | None
):
    """Remove a recipe for TARGET."""
    from lamin_cli._recipes import load_recipes, recipes_path, save_recipes

    path = recipes_path(recipe_file)
    recipes = load_recipes(path)
    matches = [r for r in recipes if r.target == target]
    if not matches:
        raise click.ClickException(f"No recipe for {target!r}.")
    if len(matches) > 1 and not required_tokens:
        shapes = ", ".join(str(r.required_tokens) for r in matches)
        raise click.ClickException(
            f"{target!r} has more than one recipe ({shapes}); pass"
            " --required-token to pick one."
        )
    to_remove = (
        matches[0]
        if not required_tokens
        else next(
            (r for r in matches if r.required_tokens == list(required_tokens)), None
        )
    )
    if to_remove is None:
        raise click.ClickException(f"No recipe for {target!r} with that shape.")
    save_recipes(path, [r for r in recipes if r is not to_remove])
    click.echo(f"removed recipe for {target!r} ({to_remove.required_tokens})")


settings.add_command(run_recipe_group)


# -----------------------------------------------------------------------------
# modules group (pattern: lamin settings modules get/set)
# -----------------------------------------------------------------------------


@click.group("modules")
def modules_group():
    """Get or set environment schema modules."""


@modules_group.command("get")
def modules_get():
    """Show current environment schema modules."""
    from lamindb_setup import settings as settings_

    modules = sorted(settings_.modules)
    click.echo(",".join(modules) if modules else "None")


@modules_group.command("set")
@click.argument("value", type=str)
def modules_set(value: str):
    """Set environment schema modules as a comma-separated string."""
    from lamindb_setup import settings as settings_

    if value.lower() == "none":
        settings_.modules = None
    else:
        settings_.modules = value


@modules_group.command("unset")
def modules_unset():
    """Unset environment schema modules."""
    from lamindb_setup import settings as settings_

    settings_.modules = None


settings.add_command(modules_group)


# -----------------------------------------------------------------------------
# Legacy get/set (hidden, backward compatibility)
# -----------------------------------------------------------------------------


@settings.command("set", hidden=True)
@click.argument(
    "setting",
    type=click.Choice(
        ["auto-connect", "private-django-api", "dev-dir", "worktree"],
        case_sensitive=False,
    ),
)
@click.argument("value")  # No explicit type - let Click handle it
def set_legacy(setting: str, value: str):
    """Set a setting (legacy). Use lamin settings <name> set <value> instead."""
    from lamindb_setup import settings as settings_

    if setting == "auto-connect":
        settings_.auto_connect = click.BOOL(value)
    if setting == "private-django-api":
        settings_.private_django_api = click.BOOL(value)
    if setting == "dev-dir":
        if value.lower() == "none":
            value = None  # type: ignore[assignment]
        settings_.dev_dir = value
    if setting == "worktree":
        _set_worktree(settings_, click.BOOL(value))


@settings.command("get", hidden=True)
@click.argument(
    "setting",
    type=click.Choice(
        [
            "auto-connect",
            "private-django-api",
            "space",
            "branch",
            "dev-dir",
            "worktree",
        ],
        case_sensitive=False,
    ),
)
def get_legacy(setting: str):
    """Get a setting (legacy). Use lamin settings <name> get instead."""
    from lamindb_setup import settings as settings_

    if setting == "branch":
        _, value = settings_._read_branch_idlike_name()
    elif setting == "space":
        _, value = settings_._read_space_idlike_name()
    elif setting == "dev-dir":
        value = settings_.dev_dir
        if value is None:
            value = "None"
    elif setting == "worktree":
        value = "true" if settings_.worktree else "false"
    else:
        value = getattr(settings_, setting.replace("-", "_"))
    click.echo(value)


# -----------------------------------------------------------------------------
# cache-dir (already uses lamin settings cache-dir get/set/clear)
# -----------------------------------------------------------------------------

from lamin_cli._cache import cache
from lamin_cli.mount import mount

settings.add_command(cache, "cache-dir")
settings.add_command(mount)
