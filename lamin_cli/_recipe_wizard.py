"""Interactive recipe creation for an ambiguous `lamin run` target.

Walking a human through a concrete argv, rather than expecting them to write
recipe JSON by hand, is the actual answer to keeping this feature usable: the
schema can be as capable as real CLIs require without that capability being a
burden, as long as nobody has to read or write it directly.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click
from lamin_utils import logger

from lamin_cli._recipes import (
    FlagRole,
    Recipe,
    add_recipe,
    apply_recipe,
)

if TYPE_CHECKING:
    from pathlib import Path


class WizardError(Exception):
    """Raised when the wizard can't run at all (e.g. no terminal)."""


def _is_flag(token: str) -> bool:
    return token.startswith("-") and token != "-"


def _looks_like_key_value(token: str) -> bool:
    """A bare "KEY=VALUE" token with no dash.

    As GATK/Picard-style tools use (`INPUT=file.bam`) instead of
    `--input file.bam`.
    """
    return not _is_flag(token) and "=" in token and not token.startswith("=")


def _unique_flags(args: list[str]) -> list[str]:
    """Every distinct flag/KEY=VALUE spelling in `args`, in first-seen order."""
    seen: list[str] = []
    for token in args:
        if _is_flag(token):
            name = token.split("=", 1)[0]
        elif _looks_like_key_value(token):
            name = token.split("=", 1)[0]
        else:
            continue
        if name not in seen:
            seen.append(name)
    return seen


def _flag_example_value(flag: str, args: list[str]) -> str | None:
    """The first plausible value seen for `flag`, to show the user context.

    Never another flag: `--foo --bar` doesn't mean `--bar` is `--foo`'s
    value, it's a sign `--foo` takes no value at all.
    """
    for i, token in enumerate(args):
        if token == flag and i + 1 < len(args) and not _is_flag(args[i + 1]):
            return args[i + 1]
        if token.startswith(f"{flag}="):
            return token.split("=", 1)[1]
    return None


def _flag_always_lacks_a_value(flag: str, args: list[str]) -> bool:
    """Whether every occurrence of `flag` is followed by nothing or a flag.

    Strong evidence it's a boolean, not that the user forgot to pass a value.
    """
    found = False
    for i, token in enumerate(args):
        if token == flag:
            found = True
            if i + 1 < len(args) and not _is_flag(args[i + 1]):
                return False
        elif token.startswith(f"{flag}="):
            return False
    return found


def _flag_has_lamin_uri_value(flag: str, args: list[str]) -> bool:
    """Whether any occurrence of `flag` is followed by a `lamin://` URI.

    An unambiguous reference to existing tracked data, so almost always an
    input rather than something to skip.
    """
    from lamin_cli._uri import is_lamin_uri

    for i, token in enumerate(args):
        if token == flag and i + 1 < len(args) and is_lamin_uri(args[i + 1]):
            return True
        if token.startswith(f"{flag}=") and is_lamin_uri(token.split("=", 1)[1]):
            return True
    return False


def _ask_flag_roles(args: list[str]) -> tuple[dict[str, FlagRole], list[str]]:
    """Walk every distinct flag once, asking what it means."""
    flag_roles: dict[str, FlagRole] = {}
    value_flags: list[str] = []
    for flag in _unique_flags(args):
        is_boolean_like = _flag_always_lacks_a_value(flag, args)
        if is_boolean_like:
            context = ""
            default = "boolean"
        else:
            example = _flag_example_value(flag, args)
            if example is None:
                context = ""
            elif f"{flag}={example}" in args:
                context = f" (e.g. {flag}={example!r})"
            else:
                context = f" (e.g. {flag} {example!r})"
            default = "input" if _flag_has_lamin_uri_value(flag, args) else "skip"
        choice = click.prompt(
            f"what does {flag!r}{context} mean?",
            type=click.Choice(["input", "output", "boolean", "skip"]),
            default=default,
            show_choices=True,
        )
        if choice in ("input", "output"):
            repeatable = click.confirm(
                f"  does {flag!r} accept multiple trailing values"
                f" (e.g. {flag} a b c), not just one?",
                default=False,
            )
            delimiter = click.prompt(
                f"  is a single {flag!r} value itself a delimited list"
                " (e.g. comma-separated)? enter the delimiter, or leave blank",
                default="",
                show_default=False,
            )
            key_template = None
            if choice == "output":
                key_template = click.prompt(
                    "  key template for this output (e.g. outputs/{name}),"
                    " or leave blank for the default",
                    default="",
                    show_default=False,
                )
            aliases = click.prompt(
                f"  any other spellings/aliases for {flag!r}? (space or comma"
                " separated), or leave blank",
                default="",
                show_default=False,
            )
            alias_list = [a for a in aliases.replace(",", " ").split() if a]
            key = "|".join([flag, *alias_list])
            flag_roles[key] = FlagRole(
                role=choice,
                repeatable=repeatable,
                delimiter=delimiter or None,
                key_template=key_template or None,
            )
        elif choice == "skip" and not is_boolean_like:
            # has a value we're choosing not to track, but it still needs to
            # be skipped correctly when counting positional/bare tokens
            value_flags.append(flag)
    return flag_roles, value_flags


def _bare_tokens_excluding(
    args: list[str], flag_roles: dict[str, FlagRole], value_flags: list[str]
) -> list[int]:
    """Indices of bare/positional tokens, given what we now know about flags."""
    by_flag: dict[str, FlagRole] = {}
    for key, role in flag_roles.items():
        for alias in key.split("|"):
            by_flag[alias] = role
    known_flags = [*by_flag, *value_flags]
    positions = []
    i = 0
    while i < len(args):
        token = args[i]
        # a fused "KEY=VALUE" token for a known flag (with or without a dash)
        # -- the value is already inside this one token, nothing more to skip
        if any(token.startswith(f"{flag}=") for flag in known_flags):
            i += 1
            continue
        if token in by_flag:
            i += 1
            if by_flag[token].repeatable:
                while i < len(args) and not _is_flag(args[i]):
                    i += 1
            elif i < len(args):
                i += 1
            continue
        if token in value_flags:
            i += 2
            continue
        if _is_flag(token):
            i += 1
            continue
        positions.append(i)
        i += 1
    return positions


def _ask_required_tokens_and_entrypoint(
    args: list[str], bare_positions: list[int]
) -> tuple[list[str], int | None]:
    required_tokens: list[str] = []
    entrypoint_positional: int | None = None
    slot = 0
    for i in bare_positions:
        token = args[i]
        choice = click.prompt(
            f"token {i} ({token!r}): part of the fixed shape (e.g. a"
            " subcommand), the actual script/entrypoint, or just a value?",
            type=click.Choice(["fixed", "entrypoint", "value"]),
            default="value",
            show_choices=True,
        )
        if choice == "fixed":
            required_tokens.append(token)
            continue
        if choice == "entrypoint":
            entrypoint_positional = slot
        slot += 1
    return required_tokens, entrypoint_positional


def _ask_extra(kind: str) -> list[str]:
    if not click.confirm(
        f"does this tool read/write any {kind} without a corresponding flag at"
        " all (a fixed filename, stdin/stdout, a derived filename)?",
        default=False,
    ):
        return []
    raw = click.prompt(
        "list them (space separated; globs OK, e.g. *.bai), or leave blank",
        default="",
        show_default=False,
    )
    return raw.split()


def _ask_command(label: str, default: list[str] | None) -> list[str] | None:
    default_str = " ".join(default) if default else ""
    prompt = f"{label} command"
    if default:
        prompt += f" (default: {default_str!r}, or type a different one)"
    else:
        prompt += " (leave blank to skip)"
    raw = click.prompt(prompt, default=default_str, show_default=False)
    if not raw:
        return None
    return raw.split()


def _print_preview(recipe: Recipe, args: list[str]) -> None:
    application = apply_recipe(recipe, args)
    logger.print("")
    logger.print("this recipe would resolve this invocation as:")
    logger.print(f"  entrypoint: {application.entrypoint!r}")
    logger.print(f"  inputs: {application.inputs}")
    logger.print(
        f"  outputs: {[(o.value, o.key_template) for o in application.outputs]}"
    )
    if application.extra_inputs:
        logger.print(f"  extra_inputs: {application.extra_inputs}")
    if application.extra_outputs:
        logger.print(f"  extra_outputs: {application.extra_outputs}")
    logger.print("")


def run_wizard(
    target: str, invoke_command: list[str], args: list[str], recipes_file: Path
) -> Recipe | None:
    """Interactively define a recipe for this invocation shape, and save it.

    `target` is the recipe's identity key (its basename, matching
    `find_matching_recipe`'s lookup); `invoke_command` is how to actually run
    it (e.g. `["./mytool"]`, since a bare basename may not resolve on PATH) --
    used only to suggest a sensible default version-probe command.

    Nothing is executed while this runs. Returns `None` if the user declines
    to save at the end, in which case the caller should fall back to today's
    behavior (no recipe) for this run.
    """
    if not sys.stdin.isatty():
        raise WizardError(
            f"No recipe matches this invocation of {target!r}, and there's no"
            " terminal to define one interactively. Run this once from an"
            " interactive shell, or point --recipe-file/$LAMIN_RUN_RECIPES at"
            " an existing recipes file that already has one."
        )

    logger.print(f"no recipe yet for {target!r} with this shape -- let's define one")
    logger.print("(nothing will be executed until this is done)")
    logger.print("")
    for i, token in enumerate(args):
        logger.print(f"  {i}: {token}")
    logger.print("")

    flag_roles, value_flags = _ask_flag_roles(args)
    bare_positions = _bare_tokens_excluding(args, flag_roles, value_flags)
    required_tokens, entrypoint_positional = _ask_required_tokens_and_entrypoint(
        args, bare_positions
    )
    extra_inputs = _ask_extra("inputs")
    extra_outputs = _ask_extra("outputs")
    version_command = _ask_command("version", [*invoke_command, "--version"])
    environment_command = _ask_command("environment (e.g. pip freeze)", None)

    from lamin_cli._run import _probe_version_command

    last_verified_version = (
        _probe_version_command(version_command) if version_command else None
    )

    recipe = Recipe(
        target=target,
        required_tokens=required_tokens,
        entrypoint_positional=entrypoint_positional,
        version_command=version_command,
        environment_command=environment_command,
        flag_roles=flag_roles,
        value_flags=value_flags,
        extra_inputs=extra_inputs,
        extra_outputs=extra_outputs,
        last_verified_version=last_verified_version,
    )
    _print_preview(recipe, args)
    if not click.confirm("save this recipe?", default=True):
        logger.print("not saved; falling back to today's behavior for this run")
        return None
    saved = add_recipe(recipes_file, recipe, overwrite=True)
    logger.print(f"saved to {recipes_file}")
    return saved
