from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner
from lamin_cli._recipe_wizard import (
    WizardError,
    _flag_always_lacks_a_value,
    _flag_example_value,
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
