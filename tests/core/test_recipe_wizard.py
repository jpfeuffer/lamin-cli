from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner
from lamin_cli._recipe_wizard import (
    WizardError,
    _flag_always_lacks_a_value,
    _flag_example_value,
    _flag_has_lamin_uri_value,
    _looks_like_key_value,
    _unique_flags,
    run_wizard,
)

ANSWERS = (
    "input\n"  # --data role
    "\n"  # --data repeatable? [N]
    "\n"  # --data delimiter?
    "\n"  # --data aliases?
    "output\n"  # --out role
    "\n"  # --out repeatable? [N]
    "\n"  # --out delimiter?
    "outputs/{name}\n"  # --out key template
    "\n"  # --out aliases?
    "fixed\n"  # token "run"
    "entrypoint\n"  # token "train.py"
    "\n"  # extra_inputs? [N]
    "\n"  # extra_outputs? [N]
    "\n"  # version_command (accept default)
    "\n"  # environment_command (skip)
    "y\n"  # save?
)

ARGS = ["run", "train.py", "--data", "d.csv", "--out", "r.csv"]


def _run_wizard_with_answers(tmp_path, answers, args=ARGS):
    runner = CliRunner()
    with runner.isolation(input=answers):
        sys.stdin.isatty = lambda: True
        return run_wizard("mytool", ["./mytool"], args, tmp_path / "recipes.json")


def test_wizard_produces_a_working_recipe(tmp_path):
    recipe = _run_wizard_with_answers(tmp_path, ANSWERS)
    assert recipe is not None
    assert recipe.target == "mytool"
    assert recipe.required_tokens == ["run"]
    assert recipe.entrypoint_positional == 0
    assert recipe.flag_roles["--data"].role == "input"
    assert recipe.flag_roles["--out"].role == "output"
    assert recipe.flag_roles["--out"].key_template == "outputs/{name}"
    # the actual invocable command, not the bare basename ("mytool" wouldn't
    # resolve on PATH the way "./mytool" does)
    assert recipe.version_command == ["./mytool", "--version"]
    assert recipe.environment_command is None


def test_wizard_saves_the_recipe_to_the_given_file(tmp_path):
    path = tmp_path / "recipes.json"
    runner = CliRunner()
    with runner.isolation(input=ANSWERS):
        sys.stdin.isatty = lambda: True
        run_wizard("mytool", ["./mytool"], ARGS, path)
    assert path.exists()
    assert "mytool" in path.read_text()


def test_declining_to_save_returns_none(tmp_path):
    declined = ANSWERS.rsplit("y\n", 1)[0] + "n\n"
    recipe = _run_wizard_with_answers(tmp_path, declined)
    assert recipe is None
    assert not (tmp_path / "recipes.json").exists()


def test_non_interactive_raises_a_clear_error(tmp_path):
    runner = CliRunner()
    with runner.isolation(input=""):
        sys.stdin.isatty = lambda: False
        with pytest.raises(WizardError, match="no terminal"):
            run_wizard("mytool", ["./mytool"], ARGS, tmp_path / "recipes.json")


# -- a flag immediately followed by another flag is a boolean, not a value ---


def test_a_flag_followed_by_another_flag_has_no_example_value():
    # `--foo --bar`: --bar is not --foo's value
    assert _flag_example_value("--foo", ["--foo", "--bar"]) is None


def test_a_flag_at_the_end_of_argv_has_no_example_value():
    assert _flag_example_value("--foo", ["--foo"]) is None


def test_a_flag_with_a_real_value_has_that_example():
    assert _flag_example_value("--data", ["--data", "d.csv"]) == "d.csv"


def test_a_flag_always_followed_by_a_flag_or_nothing_looks_boolean():
    assert _flag_always_lacks_a_value("--foo", ["--foo", "--bar"]) is True
    assert _flag_always_lacks_a_value("--foo", ["x", "--foo"]) is True


def test_a_flag_with_any_real_value_does_not_look_boolean():
    # even if one occurrence looks bare, a value anywhere is enough evidence
    assert (
        _flag_always_lacks_a_value("--foo", ["--foo", "--bar", "--foo", "val"]) is False
    )


def test_an_absent_flag_does_not_look_boolean():
    assert _flag_always_lacks_a_value("--foo", ["--bar", "x"]) is False


# -- a lamin:// URI value is unambiguously an input by default --------------


def test_a_flag_pointing_at_a_lamin_uri_is_detected():
    args = ["--data", "lamin://acme/x/artifact/key/d.csv"]
    assert _flag_has_lamin_uri_value("--data", args) is True


def test_a_flag_pointing_at_a_lamin_uri_via_equals_form_is_detected():
    args = ["--data=lamin://acme/x/artifact/key/d.csv"]
    assert _flag_has_lamin_uri_value("--data", args) is True


def test_a_flag_pointing_at_a_plain_path_is_not_a_lamin_uri():
    assert _flag_has_lamin_uri_value("--data", ["--data", "plain.csv"]) is False


def test_wizard_defaults_a_lamin_uri_flag_to_input(tmp_path):
    args = [
        "run",
        "train.py",
        "--data",
        "lamin://acme/x/artifact/key/d.csv",
        "--out",
        "r.csv",
    ]
    # accepting every default: --data should resolve to "input" without
    # typing anything for its role
    answers = (
        "\n"  # --data role: accept default (should be "input")
        "\n"  # --data repeatable? [N]
        "\n"  # --data delimiter?
        "\n"  # --data aliases?
        "skip\n"  # --out role (not exercising output here)
        "fixed\n"  # token "run"
        "entrypoint\n"  # token "train.py"
        "\n"  # extra_inputs? [N]
        "\n"  # extra_outputs? [N]
        "\n"  # version_command
        "\n"  # environment_command
        "y\n"  # save?
    )
    recipe = _run_wizard_with_answers(tmp_path, answers, args=args)
    assert recipe.flag_roles["--data"].role == "input"


def test_a_skipped_but_value_bearing_flag_does_not_corrupt_positional_counting(
    tmp_path,
):
    """Regression test: choosing "skip" for a flag with a real value (--out
    here) must still register it as a value-consumer, or its value gets
    mistaken for an unrelated positional/entrypoint token.
    """
    args = [
        "run",
        "train.py",
        "--data",
        "lamin://acme/x/artifact/key/d.csv",
        "--out",
        "r.csv",
    ]
    answers = (
        "\n"  # --data role: accept default ("input")
        "\n"  # --data repeatable?
        "\n"  # --data delimiter?
        "\n"  # --data aliases?
        "skip\n"  # --out role: has a value, but not tracked
        "fixed\n"  # token "run"
        "entrypoint\n"  # token "train.py" -- only 2 bare tokens, not 3
        "\n"  # extra_inputs?
        "\n"  # extra_outputs?
        "\n"  # version_command
        "\n"  # environment_command
        "y\n"  # save?
    )
    recipe = _run_wizard_with_answers(tmp_path, answers, args=args)
    assert recipe is not None
    assert "--out" in recipe.value_flags
    assert recipe.entrypoint_positional == 0


# -- GATK/Picard-style "KEY=VALUE" options, no dash at all -------------------


def test_key_value_style_tokens_are_recognized_as_flags():
    assert _looks_like_key_value("INPUT=in.bam") is True
    assert _looks_like_key_value("--flag") is False  # dashed, handled already
    assert _looks_like_key_value("--flag=value") is False  # already a flag
    assert _looks_like_key_value("plain_value") is False  # no "="


def test_unique_flags_discovers_key_value_tokens_too():
    args = ["SomeTool", "INPUT=in.bam", "OUTPUT=out.bam"]
    assert _unique_flags(args) == ["INPUT", "OUTPUT"]


def test_wizard_asks_about_key_value_flags_and_defaults_correctly(tmp_path):
    args = [
        "SomeTool",
        "INPUT=lamin://acme/x/artifact/key/d.csv",
        "OUTPUT=r.csv",
        "train.py",
    ]
    answers = (
        "\n"  # INPUT role: accept default ("input", it's a lamin:// URI)
        "\n"  # INPUT repeatable?
        "\n"  # INPUT delimiter?
        "\n"  # INPUT aliases?
        "skip\n"  # OUTPUT role: not exercising output here
        "fixed\n"  # token "SomeTool"
        "entrypoint\n"  # token "train.py" -- OUTPUT=r.csv's value must not
        # show up as a separate third bare token here
        "\n"  # extra_inputs?
        "\n"  # extra_outputs?
        "\n"  # version_command
        "\n"  # environment_command
        "y\n"  # save?
    )
    recipe = _run_wizard_with_answers(tmp_path, answers, args=args)
    assert recipe is not None
    assert recipe.flag_roles["INPUT"].role == "input"
    assert "OUTPUT" in recipe.value_flags
    assert recipe.required_tokens == ["SomeTool"]
    assert recipe.entrypoint_positional == 0


# -- a bare positional (no flag) can be marked input/output too -------------


def test_wizard_supports_purely_positional_input_and_output(tmp_path):
    # `samtools sort in.bam out.bam`: no flags at all
    args = ["sort", "in.bam", "out.bam"]
    answers = (
        "fixed\n"  # token "sort"
        "input\n"  # token "in.bam"
        "output\n"  # token "out.bam"
        "outputs/{name}\n"  # key template for that output
        "\n"  # extra_inputs?
        "\n"  # extra_outputs?
        "\n"  # version_command
        "\n"  # environment_command
        "y\n"  # save?
    )
    recipe = _run_wizard_with_answers(tmp_path, answers, args=args)
    assert recipe is not None
    assert recipe.required_tokens == ["sort"]
    assert recipe.positional_roles[0].role == "input"
    assert recipe.positional_roles[1].role == "output"
    assert recipe.positional_roles[1].key_template == "outputs/{name}"

    from lamin_cli._recipes import apply_recipe

    application = apply_recipe(recipe, args)
    assert application.inputs == ["in.bam"]
    assert [o.value for o in application.outputs] == ["out.bam"]
