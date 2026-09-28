from __future__ import annotations

import json

import pytest
from lamin_cli import _recipes as recipes_mod
from lamin_cli._recipes import (
    FlagRole,
    Recipe,
    add_recipe,
    apply_recipe,
    find_matching_recipe,
    load_recipes,
    recipes_path,
    save_recipes,
)

# -- the exact motivating example from the design discussion -----------------


def _footool_recipe() -> Recipe:
    return Recipe(
        target="foo",
        required_tokens=["info", "runs"],
        flag_roles={
            "--in": FlagRole(role="input", repeatable=True),
            "-in2": FlagRole(role="input"),
            "-out": FlagRole(role="output", key_template="{name}"),
        },
    )


def test_matches_regardless_of_flag_order_and_unrelated_flags():
    recipe = _footool_recipe()
    args = [
        "-v",
        "info",
        "runs",
        "--in",
        "foo.txt",
        "-in2",
        "bar.txt",
        "-out",
        "folderfoo/",
    ]
    assert find_matching_recipe("foo", args, [recipe]) is recipe

    # flags reordered, an unrelated flag inserted: still matches
    reordered = [
        "-out",
        "folderfoo/",
        "-in2",
        "bar.txt",
        "-v",
        "--in",
        "foo.txt",
        "info",
        "runs",
    ]
    assert find_matching_recipe("foo", reordered, [recipe]) is recipe


def test_subcommand_order_matters_unlike_flags():
    recipe = _footool_recipe()
    # "runs" before "info": a genuinely different invocation, must not match
    assert find_matching_recipe("foo", ["runs", "info"], [recipe]) is None


def test_a_different_target_never_matches():
    recipe = _footool_recipe()
    assert find_matching_recipe("bar", ["info", "runs"], [recipe]) is None


def test_repeatable_flag_collects_all_values_until_the_next_flag():
    recipe = _footool_recipe()
    args = ["info", "runs", "--in", "a.txt", "b.txt", "c.txt", "-out", "result.csv"]
    application = apply_recipe(recipe, args)
    assert application.inputs == ["a.txt", "b.txt", "c.txt"]
    assert [o.value for o in application.outputs] == ["result.csv"]
    assert application.outputs[0].key_template == "{name}"


def test_flag_equals_value_form_is_recognized():
    recipe = _footool_recipe()
    args = ["info", "runs", "-in2=bar.txt", "-out=result.csv"]
    application = apply_recipe(recipe, args)
    assert application.inputs == ["bar.txt"]
    assert [o.value for o in application.outputs] == ["result.csv"]


def test_flag_aliases_joined_by_pipe_are_all_recognized():
    # a tool that accepts both spellings for the same flag
    recipe = Recipe(
        target="tool",
        flag_roles={"-o|--output": FlagRole(role="output", key_template="{name}")},
    )
    via_short = apply_recipe(recipe, ["-o", "a.txt"])
    via_long = apply_recipe(recipe, ["--output", "a.txt"])
    assert [o.value for o in via_short.outputs] == ["a.txt"]
    assert [o.value for o in via_long.outputs] == ["a.txt"]
    # the canonical (joined) key is reported as the source either way
    assert via_short.outputs[0].source == "-o|--output"
    assert via_long.outputs[0].source == "-o|--output"


def test_delimiter_splits_a_single_value_into_multiple():
    recipe = Recipe(
        target="tool",
        flag_roles={"--in": FlagRole(role="input", delimiter=",")},
    )
    application = apply_recipe(recipe, ["--in", "a.txt,b.txt,c.txt"])
    assert application.inputs == ["a.txt", "b.txt", "c.txt"]


def test_delimiter_and_repeatable_can_combine():
    # "--in a.txt,x.txt b.txt" -> both a repeated flag AND a delimited value
    recipe = Recipe(
        target="tool",
        flag_roles={"--in": FlagRole(role="input", repeatable=True, delimiter=",")},
    )
    application = apply_recipe(recipe, ["--in", "a.txt,x.txt", "b.txt"])
    assert application.inputs == ["a.txt", "x.txt", "b.txt"]


def test_an_undeclared_flag_spelling_is_simply_untracked_not_an_error():
    # using "-out" when only "--output" is declared: no crash, just unlinked
    recipe = Recipe(
        target="tool",
        flag_roles={"--output": FlagRole(role="output")},
    )
    application = apply_recipe(recipe, ["-out", "a.txt"])
    assert application.outputs == []


def test_entrypoint_flag_overrides_transform_identity():
    recipe = Recipe(
        target="uv",
        required_tokens=["run"],
        entrypoint_flag="--script",
        flag_roles={"--data": FlagRole(role="input")},
    )
    args = [
        "run",
        "--script",
        "train.py",
        "--data",
        "lamin://acme/x/artifact/key/d.csv",
    ]
    application = apply_recipe(recipe, args)
    assert application.entrypoint == "train.py"
    assert application.inputs == ["lamin://acme/x/artifact/key/d.csv"]


def test_no_entrypoint_flag_means_no_override():
    recipe = _footool_recipe()
    application = apply_recipe(recipe, ["info", "runs"])
    assert application.entrypoint is None


def test_entrypoint_positional_finds_the_bare_token_after_required_tokens():
    # the common launcher case: no flag at all, e.g. `uv run script.py`
    recipe = Recipe(target="uv", required_tokens=["run"], entrypoint_positional=0)
    application = apply_recipe(recipe, ["run", "script.py"])
    assert application.entrypoint == "script.py"


def test_entrypoint_positional_skips_flags_and_their_values():
    # --project takes a value we don't care about; without knowing that, its
    # value "x" would be mistaken for a bare/positional token
    recipe = Recipe(
        target="uv",
        required_tokens=["run"],
        entrypoint_positional=0,
        value_flags=["--project"],
    )
    application = apply_recipe(recipe, ["-v", "run", "--project", "x", "script.py"])
    assert application.entrypoint == "script.py"


def test_entrypoint_positional_also_skips_flag_roles_values():
    # a flag already tracked via flag_roles must be skipped too, not just
    # value_flags -- both mechanisms need to cooperate
    recipe = Recipe(
        target="uv",
        required_tokens=["run"],
        entrypoint_positional=0,
        flag_roles={"--data": FlagRole(role="input")},
    )
    application = apply_recipe(recipe, ["run", "--data", "d.csv", "script.py"])
    assert application.entrypoint == "script.py"


def test_entrypoint_flag_takes_precedence_over_entrypoint_positional():
    recipe = Recipe(
        target="uv",
        required_tokens=["run"],
        entrypoint_flag="--script",
        entrypoint_positional=0,
    )
    application = apply_recipe(recipe, ["run", "--script", "real.py", "decoy.py"])
    assert application.entrypoint == "real.py"


def test_resolved_output_carries_its_source_flag():
    recipe = _footool_recipe()
    application = apply_recipe(recipe, ["info", "runs", "-out", "result.csv"])
    assert application.outputs[0].source == "-out"


def test_extra_inputs_and_outputs_are_independent_of_argv():
    recipe = Recipe(
        target="aligner",
        required_tokens=[],
        extra_inputs=["reference.fasta"],
        extra_outputs=["*.bai"],
    )
    application = apply_recipe(recipe, ["some", "args"])
    assert application.extra_inputs == ["reference.fasta"]
    assert application.extra_outputs == ["*.bai"]


# -- GATK/Picard-style "KEY=VALUE" options, no dash at all -------------------


def test_key_value_style_options_are_recognized_without_a_dash():
    recipe = Recipe(
        target="gatk",
        flag_roles={
            "INPUT": FlagRole(role="input"),
            "OUTPUT": FlagRole(role="output", key_template="{name}"),
        },
    )
    args = ["INPUT=in.bam", "OUTPUT=out.bam"]
    application = apply_recipe(recipe, args)
    assert application.inputs == ["in.bam"]
    assert [o.value for o in application.outputs] == ["out.bam"]


def test_key_value_entrypoint_positional_is_not_corrupted_by_fused_values():
    # the entrypoint is a bare positional; a KEY=VALUE token elsewhere must
    # not be mistaken for one, the way a naive dash-only check would
    recipe = Recipe(
        target="gatk",
        required_tokens=["SomeTool"],
        entrypoint_positional=0,
        flag_roles={"INPUT": FlagRole(role="input")},
    )
    args = ["SomeTool", "INPUT=in.bam", "train.py"]
    application = apply_recipe(recipe, args)
    assert application.entrypoint == "train.py"


def test_a_tool_with_no_flags_at_all_still_matches_by_required_tokens_only():
    recipe = Recipe(target="samtools", required_tokens=["sort"])
    assert find_matching_recipe("samtools", ["sort", "in.bam"], [recipe]) is recipe


# -- storage: round-trip, atomic write, path resolution, dedup --------------


def test_round_trips_through_json(tmp_path):
    path = tmp_path / "run-recipes.json"
    recipe = _footool_recipe()
    save_recipes(path, [recipe])
    loaded = load_recipes(path)
    assert loaded == [recipe]
    # human-inspectable, not opaque
    assert "flag_roles" in json.loads(path.read_text())["recipes"][0]


def test_loading_a_missing_file_returns_no_recipes(tmp_path):
    assert load_recipes(tmp_path / "does-not-exist.json") == []


def test_save_is_atomic_no_leftover_temp_file(tmp_path):
    path = tmp_path / "run-recipes.json"
    save_recipes(path, [_footool_recipe()])
    leftovers = list(tmp_path.glob(".run-recipes-*.tmp"))
    assert leftovers == []


def test_add_recipe_reuses_an_identical_one_rather_than_duplicating(tmp_path):
    path = tmp_path / "run-recipes.json"
    first = add_recipe(path, _footool_recipe())
    second = add_recipe(path, _footool_recipe())
    assert first == second
    assert len(load_recipes(path)) == 1


def test_add_recipe_keeps_distinct_shapes_of_the_same_target_separate(tmp_path):
    path = tmp_path / "run-recipes.json"
    add_recipe(path, Recipe(target="foo", required_tokens=["info", "runs"]))
    add_recipe(path, Recipe(target="foo", required_tokens=["build"]))
    assert len(load_recipes(path)) == 2


@pytest.mark.parametrize(
    ("explicit", "env", "expect_default"),
    [
        (None, None, True),
        (None, "env-path.json", False),
        ("explicit-path.json", "env-path.json", False),
    ],
)
def test_recipes_path_precedence(monkeypatch, explicit, env, expect_default):
    if env is not None:
        monkeypatch.setenv(recipes_mod.RECIPES_ENV, env)
    else:
        monkeypatch.delenv(recipes_mod.RECIPES_ENV, raising=False)
    resolved = recipes_path(explicit)
    if expect_default:
        assert resolved == recipes_mod.default_recipes_path()
    elif explicit is not None:
        assert resolved.name == "explicit-path.json"
    else:
        assert resolved.name == "env-path.json"
