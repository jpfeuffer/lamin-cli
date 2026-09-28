"""Recipes: how to interpret an ambiguous `lamin run` target's argv.

A recipe is personal, cross-project tool knowledge ("--in means input for this
tool"), not project data, so it lives in a local, per-machine JSON file by
default rather than a repo file or a lamindb record. See the design notes for
why (session-local, not part of this repo).

Matching deliberately doesn't parse CLI grammar (subcommand vs. flag vs.
positional): a recipe's `required_tokens` must appear, in order, among the
bare (non-flag) tokens of an invocation; flag roles are assigned by flag name
regardless of position. This tolerates reordering and new unrelated flags
without needing to understand the tool's grammar at all.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

RECIPES_ENV = "LAMIN_RUN_RECIPES"


@dataclass
class FlagRole:
    """What a flag's value(s) mean."""

    role: Literal["input", "output", "output_prefix"]
    repeatable: bool = False
    # a single value is itself a delimited list, e.g. "--in a.txt,b.txt" ->
    # ["a.txt", "b.txt"]. Independent of `repeatable` (that's for repeated
    # occurrences/trailing values; this is for one value packed together).
    delimiter: str | None = None
    # for outputs: a key template, e.g. "outputs/{name}"; {name} is the
    # basename of the value this flag pointed to. Unused for output_prefix
    # (each matched file gets its own default cwd-relative key).
    key_template: str | None = None


@dataclass
class Recipe:
    """How to interpret one shape of invocation of `target`."""

    target: str
    required_tokens: list[str] = field(default_factory=list)
    # a flag name (e.g. "--script") whose value is the entrypoint/identity,
    # overriding the target's own name as the transform's key; None means no
    # flag-based entrypoint (see entrypoint_positional below)
    entrypoint_flag: str | None = None
    # the more common case for launchers: a bare positional with no flag at
    # all (`uv run script.py`, `python foo.py`). 0-based index among the bare
    # tokens that come *after* required_tokens. entrypoint_flag takes
    # precedence if both are set; neither set means the target itself is the
    # identity.
    entrypoint_positional: int | None = None
    # a bare positional (no flag at all) that's an input/output, e.g.
    # `samtools sort in.bam out.bam` or `cp src dest` -- keyed the same way
    # as entrypoint_positional: 0-based index among the bare tokens after
    # required_tokens.
    positional_roles: dict[int, FlagRole] = field(default_factory=dict)
    version_command: list[str] | None = None
    environment_command: list[str] | None = None
    flag_roles: dict[str, FlagRole] = field(default_factory=dict)
    # other flags that take a value but aren't an input/output -- not tracked
    # otherwise, but needed so a positional entrypoint (see above) isn't
    # thrown off by mistaking that value for a bare/positional token. There's
    # no way to infer this without knowing the tool's grammar, so it must be
    # declared explicitly.
    value_flags: list[str] = field(default_factory=list)
    # fixed paths or glob patterns the tool reads/writes without a
    # corresponding flag at all (stdin/stdout, a fixed filename, a derived
    # filename convention)
    extra_inputs: list[str] = field(default_factory=list)
    extra_outputs: list[str] = field(default_factory=list)
    last_verified_version: str | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["flag_roles"] = {
            flag: asdict(role) for flag, role in self.flag_roles.items()
        }
        # JSON object keys must be strings
        data["positional_roles"] = {
            str(index): asdict(role) for index, role in self.positional_roles.items()
        }
        return data

    @classmethod
    def from_dict(cls, data: dict) -> Recipe:
        data = dict(data)
        flag_roles = {
            flag: FlagRole(**role) for flag, role in data.get("flag_roles", {}).items()
        }
        data["flag_roles"] = flag_roles
        positional_roles = {
            int(index): FlagRole(**role)
            for index, role in data.get("positional_roles", {}).items()
        }
        data["positional_roles"] = positional_roles
        return cls(**data)


def default_recipes_path() -> Path:
    from lamindb_setup.core._settings_store import settings_dir

    return Path(settings_dir) / "run-recipes.json"


def recipes_path(explicit: str | None = None) -> Path:
    """Resolve the recipes file.

    Precedence: explicit path, then $LAMIN_RUN_RECIPES, then the local
    per-machine default.
    """
    if explicit is not None:
        return Path(explicit)
    env_value = os.environ.get(RECIPES_ENV)
    if env_value:
        return Path(env_value)
    return default_recipes_path()


def load_recipes(path: Path) -> list[Recipe]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return [Recipe.from_dict(entry) for entry in data.get("recipes", [])]


def save_recipes(path: Path, recipes: list[Recipe]) -> None:
    """Write atomically (temp file + rename).

    A crash mid-write never corrupts the file, and concurrent readers never
    see a partial write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"recipes": [recipe.to_dict() for recipe in recipes]}
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, prefix=".run-recipes-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        Path(tmp_path).replace(path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def add_recipe(path: Path, new_recipe: Recipe, *, overwrite: bool = False) -> Recipe:
    """Save `new_recipe`, reusing an identical one that appeared concurrently.

    Mirrors how lamindb itself reuses an existing Transform by hash rather
    than creating a duplicate: two `lamin run` invocations racing to define a
    recipe for the same shape should converge on one, not fork into two.

    `overwrite`, for someone deliberately redefining an existing recipe (the
    wizard's `--new-recipe`), replaces a match instead of reusing it.
    """
    recipes = load_recipes(path)
    for i, existing in enumerate(recipes):
        if (
            existing.target == new_recipe.target
            and existing.required_tokens == new_recipe.required_tokens
        ):
            if not overwrite:
                return existing
            recipes[i] = new_recipe
            save_recipes(path, recipes)
            return new_recipe
    recipes.append(new_recipe)
    save_recipes(path, recipes)
    return new_recipe


def _is_flag(token: str) -> bool:
    return token.startswith("-") and token != "-"


def _bare_tokens(args: list[str]) -> list[str]:
    """Non-flag tokens, for matching a recipe's `required_tokens`.

    Doesn't attempt to skip an unrelated flag's *value* (we don't know which
    unknown flags take one) -- but that's fine here: matching only checks
    that `required_tokens` appear as a subsequence, and tolerates extra noise
    in between. `_bare_tokens_for_recipe` below is the precise version, used
    once a specific recipe is known.
    """
    return [token for token in args if not _is_flag(token)]


def _expand_flag_roles(
    flag_roles: dict[str, FlagRole],
) -> dict[str, tuple[str, FlagRole]]:
    """Map every individual flag spelling to its role and canonical key.

    A `flag_roles` key can list aliases joined by "|" (e.g. "-o|--output")
    for a tool that accepts more than one spelling of the same flag; this
    expands that into a lookup by each individual spelling, keeping the
    original joined key as the canonical identifier (e.g. for
    `--output-key` to target).
    """
    expanded: dict[str, tuple[str, FlagRole]] = {}
    for key, role in flag_roles.items():
        for alias in key.split("|"):
            expanded[alias] = (key, role)
    return expanded


def _bare_tokens_for_recipe(args: list[str], recipe: Recipe) -> list[str]:
    """Non-flag, non-flag-value tokens, using this recipe's own flags.

    Needed for `entrypoint_positional`, which -- unlike matching -- needs the
    *exact* count of positional tokens, not just their presence.
    """
    by_flag = _expand_flag_roles(recipe.flag_roles)
    known_flags = [*by_flag, *recipe.value_flags]
    bare = []
    i = 0
    while i < len(args):
        token = args[i]
        # a fused "KEY=VALUE" token for a known flag (with or without a dash,
        # e.g. GATK/Picard-style "INPUT=file.bam" as well as "--flag=value")
        # -- the value is already inside this one token, nothing more to skip
        if any(token.startswith(f"{flag}=") for flag in known_flags):
            i += 1
            continue
        if token in by_flag:
            i += 1
            if by_flag[token][1].repeatable:
                while i < len(args) and not _is_flag(args[i]):
                    i += 1
            elif i < len(args):
                i += 1
            continue
        if token in recipe.value_flags:
            i += 2
            continue
        if _is_flag(token):
            i += 1
            continue
        bare.append(token)
        i += 1
    return bare


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    it = iter(haystack)
    return all(token in it for token in needle)


def _remaining_after_required(required_tokens: list[str], bare: list[str]) -> list[str]:
    """Bare tokens still left after greedily consuming `required_tokens`.

    Used to find a positional entrypoint like the script in `uv run
    script.py`: nothing named it, it's just whatever bare token comes after
    the subcommand chain.
    """
    idx = 0
    for token in required_tokens:
        while idx < len(bare) and bare[idx] != token:
            idx += 1
        if idx == len(bare):
            return []
        idx += 1
    return bare[idx:]


def find_matching_recipe(
    target: str, args: list[str], recipes: list[Recipe]
) -> Recipe | None:
    """The matching recipe, if any.

    A recipe matches when its target matches and its `required_tokens` appear,
    in order, among the bare tokens of `args`. Flags are irrelevant to
    matching entirely (order-independent, and unrecognized ones don't break a
    match) -- only the bare/subcommand-like token sequence is checked.
    """
    bare = _bare_tokens(args)
    candidates = [recipe for recipe in recipes if recipe.target == target]
    # prefer the most specific match (longest required_tokens) first
    for recipe in sorted(candidates, key=lambda r: -len(r.required_tokens)):
        if _is_subsequence(recipe.required_tokens, bare):
            return recipe
    return None


@dataclass
class ResolvedOutput:
    value: str
    key_template: str | None = None
    # the flag that produced this output, or the glob pattern for an
    # extra_output -- an identifier `--output-key` can target for an override
    source: str | None = None


@dataclass
class RecipeApplication:
    """What a recipe means for one concrete invocation."""

    entrypoint: str | None
    inputs: list[str]
    outputs: list[ResolvedOutput]
    extra_inputs: list[str]
    extra_outputs: list[str]


def _flag_values(flags: list[str], args: list[str], *, repeatable: bool) -> list[str]:
    """All values passed to any of `flags` (aliases of the same flag).

    Wherever they appear, in any order.
    """
    values: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token in flags:
            i += 1
            if repeatable:
                while i < len(args) and not _is_flag(args[i]):
                    values.append(args[i])
                    i += 1
                continue
            if i < len(args):
                values.append(args[i])
                i += 1
            continue
        if any(token.startswith(f"{flag}=") for flag in flags):
            values.append(token.split("=", 1)[1])
        i += 1
    return values


def apply_recipe(recipe: Recipe, args: list[str]) -> RecipeApplication:
    """Resolve a matched recipe's roles against a concrete invocation's argv."""
    remaining = None
    if recipe.entrypoint_positional is not None or recipe.positional_roles:
        remaining = _remaining_after_required(
            recipe.required_tokens, _bare_tokens_for_recipe(args, recipe)
        )

    entrypoint = None
    if recipe.entrypoint_flag is not None:
        found = _flag_values(recipe.entrypoint_flag.split("|"), args, repeatable=False)
        entrypoint = found[0] if found else None
    elif recipe.entrypoint_positional is not None and remaining is not None:
        if recipe.entrypoint_positional < len(remaining):
            entrypoint = remaining[recipe.entrypoint_positional]

    inputs: list[str] = []
    outputs: list[ResolvedOutput] = []
    prefix_patterns: list[str] = []
    for key, role in recipe.flag_roles.items():
        values = _flag_values(key.split("|"), args, repeatable=role.repeatable)
        if role.delimiter:
            values = [item for value in values for item in value.split(role.delimiter)]
        if role.role == "input":
            inputs.extend(values)
        elif role.role == "output":
            outputs.extend(
                ResolvedOutput(value=value, key_template=role.key_template, source=key)
                for value in values
            )
        else:  # output_prefix: everything starting with this value, e.g. a
            # tool that writes "{prefix}.R1.fastq", "{prefix}.log", etc.
            prefix_patterns.extend(f"{value}*" for value in values)

    if remaining is not None:
        for index, role in recipe.positional_roles.items():
            if index >= len(remaining):
                continue
            value = remaining[index]
            if role.role == "input":
                inputs.append(value)
            elif role.role == "output":
                outputs.append(
                    ResolvedOutput(
                        value=value, key_template=role.key_template, source=f"@{index}"
                    )
                )
            else:  # output_prefix
                prefix_patterns.append(f"{value}*")

    return RecipeApplication(
        entrypoint=entrypoint,
        inputs=inputs,
        outputs=outputs,
        extra_inputs=list(recipe.extra_inputs),
        extra_outputs=[*recipe.extra_outputs, *prefix_patterns],
    )
