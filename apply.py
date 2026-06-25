#!/usr/bin/env python3
"""
AI Skills CLI — run any skill in this repository from any working directory.

Commands:
    list                            List all available skills with status
    inspect <skill>                 Print skill metadata and input/output schema
    run <skill> [--field value ...]  Run a skill (field args map to input fields)
    run <skill> --json '{"k":"v"}'  Run a skill with raw JSON input

Environment:
    ANTHROPIC_API_KEY               Shared key — auto-aliased to the skill's prefix.
    SKILL_<NAME>_API_KEY            Skill-specific key (takes precedence).

Examples:
    python apply.py list
    python apply.py inspect summarize_document
    python apply.py run summarize_document --text "Q3 revenue rose 12%..."
    python apply.py run summarize_document --text "..." --max-sentences 3
    python apply.py run summarize_document --json '{"text":"...","max_sentences":3}'
    python apply.py run summarize_document --text "..." --json-output
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

# ── Bootstrap: make `skills.*` importable from any working directory ──────────
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

SKILLS_DIR = REPO_ROOT / "skills"

# ─────────────────────────────────────────────────────────────────────────────
# Skill discovery
# ─────────────────────────────────────────────────────────────────────────────

def _discover_skills() -> dict[str, dict[str, Any]]:
    """Return {skill_id: metadata} for every valid skill directory."""
    skills: dict[str, dict[str, Any]] = {}
    if not SKILLS_DIR.exists():
        return skills
    for path in sorted(SKILLS_DIR.iterdir()):
        meta_file = path / "metadata.json"
        if path.is_dir() and not path.name.startswith("_") and meta_file.exists():
            try:
                with meta_file.open(encoding="utf-8") as fh:
                    skills[path.name] = json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
    return skills


def _require_skill(skills: dict[str, Any], name: str) -> dict[str, Any]:
    if name not in skills:
        _die(
            f"Skill '{name}' not found.\n"
            f"Available: {', '.join(skills) or '(none)'}\n"
            "Run `apply.py list` to see all skills."
        )
    return skills[name]


def _die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


# ─────────────────────────────────────────────────────────────────────────────
# API key resolution
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_api_key(skill_id: str) -> None:
    """
    Ensure the skill-specific API key env var is set.
    Falls back to ANTHROPIC_API_KEY if the skill-specific one is absent.
    Exits with an error if neither is available.
    """
    prefix = f"SKILL_{skill_id.upper()}_"
    specific_var = f"{prefix}API_KEY"

    if os.environ.get(specific_var):
        return  # already set

    shared_key = os.environ.get("ANTHROPIC_API_KEY")
    if not shared_key:
        _die(
            f"No API key found.\n"
            f"Set either {specific_var} or ANTHROPIC_API_KEY before running a skill."
        )

    # Alias the shared key to the skill-specific prefix for this process
    os.environ[specific_var] = shared_key


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic input parsing
# ─────────────────────────────────────────────────────────────────────────────

def _parse_extra_fields(extra: list[str]) -> dict[str, Any]:
    """
    Convert a list of ['--text', 'hello', '--max-sentences', '3'] into
    {'text': 'hello', 'max_sentences': 3}.

    Values that parse as JSON primitives (int, float, bool, null) are cast;
    everything else stays as a string.
    """
    result: dict[str, Any] = {}
    i = 0
    while i < len(extra):
        token = extra[i]
        if not token.startswith("--"):
            i += 1
            continue
        key = token[2:].replace("-", "_")
        if i + 1 < len(extra) and not extra[i + 1].startswith("--"):
            raw = extra[i + 1]
            try:
                result[key] = json.loads(raw)
            except json.JSONDecodeError:
                result[key] = raw
            i += 2
        else:
            result[key] = True
            i += 1
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Output formatting
# ─────────────────────────────────────────────────────────────────────────────

_BOLD  = "\033[1m"
_RESET = "\033[0m"
_GREEN = "\033[32m"
_GRAY  = "\033[90m"

def _b(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}"


def _pretty_print(output: Any) -> None:
    data: dict[str, Any] = output.model_dump()
    for key, value in data.items():
        if isinstance(value, list):
            print(f"\n{_b(key)}:")
            for item in value:
                wrapped = textwrap.fill(str(item), width=88, initial_indent="  • ", subsequent_indent="    ")
                print(wrapped)
        elif key == "result":
            print(f"\n{_b(key)}:")
            wrapped = textwrap.fill(str(value), width=88, initial_indent="  ", subsequent_indent="  ")
            print(wrapped)
        elif isinstance(value, float):
            bar = "█" * int(value * 20) + "░" * (20 - int(value * 20))
            print(f"{_b(key)}: {value:.2f}  {_GRAY}[{bar}]{_RESET}")
        else:
            print(f"{_b(key)}: {value}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Sub-commands
# ─────────────────────────────────────────────────────────────────────────────

def cmd_list(_args: argparse.Namespace) -> None:
    skills = _discover_skills()
    if not skills:
        print(f"No skills found in {SKILLS_DIR}")
        return

    status_colour = {"stable": "\033[32m", "experimental": "\033[33m", "deprecated": "\033[31m"}
    reset = "\033[0m"

    print(f"\n{'Skill':<28} {'Version':<10} {'Status':<14} Description")
    print("─" * 100)
    for skill_id, meta in skills.items():
        status = meta.get("status", "unknown")
        colour = status_colour.get(status, "")
        desc = meta.get("description", "")
        if len(desc) > 52:
            desc = desc[:49] + "..."
        print(f"{skill_id:<28} {meta.get('version',''):<10} {colour}{status:<14}{reset} {desc}")
    print()


def cmd_inspect(args: argparse.Namespace) -> None:
    skills = _discover_skills()
    meta = _require_skill(skills, args.skill)

    print(f"\n{_b(meta['display_name'])}  v{meta['version']}  [{meta['status']}]")
    print(f"  {meta.get('description', '')}\n")

    # Input fields
    fields = meta.get("interface", {}).get("input_fields", [])
    if fields:
        print(_b("Input fields:"))
        for f in fields:
            req = "(required)" if f.get("required") else f"(default: {f.get('default', '?')})"
            extra = ""
            if f.get("max_length"):
                extra += f"  max_length={f['max_length']}"
            if f.get("range"):
                extra += f"  range={f['range']}"
            print(f"  --{f['name']:<20} {f['type']:<10} {req}{extra}")

    # Output fields
    out_fields = meta.get("interface", {}).get("output_fields", [])
    if out_fields:
        print(f"\n{_b('Output fields:')}")
        for f in out_fields:
            print(f"  {f['name']:<22} {f['type']}")

    # Security summary
    sec = meta.get("security", {})
    if sec:
        print(f"\n{_b('Security:')}")
        print(f"  active threats : {', '.join(sec.get('active_threats', []))}")
        print(f"  sanitize fields: {', '.join(sec.get('sanitize_fields', []))}")
        print(f"  pii patterns   : {', '.join(sec.get('pii_pattern_types', []))}")

    # Example usage
    ex_field = fields[0]["name"] if fields else "text"
    print(f"\n{_b('Example:')}")
    print(f"  python {REPO_ROOT}/apply.py run {args.skill} --{ex_field} 'Your input here'")
    print()


def cmd_run(args: argparse.Namespace, extra: list[str]) -> None:
    skills = _discover_skills()
    _require_skill(skills, args.skill)

    # Build input dict
    if args.json:
        try:
            input_dict: dict[str, Any] = json.loads(args.json)
        except json.JSONDecodeError as e:
            _die(f"Invalid JSON in --json: {e}")
    else:
        input_dict = _parse_extra_fields(extra)

    if not input_dict:
        meta = skills[args.skill]
        fields = meta.get("interface", {}).get("input_fields", [])
        example_field = fields[0]["name"] if fields else "text"
        _die(
            "No input provided.\n"
            f"Example: python apply.py run {args.skill} --{example_field} 'Your input here'\n"
            f"Or:      python apply.py run {args.skill} --json '{{\"text\": \"...\"}}'",
        )

    # Resolve API key
    _resolve_api_key(args.skill)

    # Dynamically import and run
    try:
        module = importlib.import_module(f"skills.{args.skill}")
    except ModuleNotFoundError as e:
        _die(f"Could not import skill '{args.skill}': {e}")

    run_fn = getattr(module, "run", None)
    if not callable(run_fn):
        _die(f"Skill '{args.skill}' does not export a `run()` function.")

    try:
        output = run_fn(input_dict)
    except Exception as e:
        _die(f"{type(e).__name__}: {e}")

    if args.json_output:
        print(json.dumps(output.model_dump(), indent=2))
    else:
        _pretty_print(output)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apply.py",
        description="AI Skills CLI — run skills from this repository from any directory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python apply.py list
              python apply.py inspect summarize_document
              python apply.py run summarize_document --text "Q3 revenue rose 12%..."
              python apply.py run summarize_document --text "..." --max-sentences 3
              python apply.py run summarize_document --json '{"text":"...","max_sentences":3}'
              python apply.py run summarize_document --text "..." --json-output
        """),
    )
    subs = parser.add_subparsers(dest="command", required=True)

    subs.add_parser("list", help="List all available skills")

    sp_inspect = subs.add_parser("inspect", help="Print skill metadata and field schema")
    sp_inspect.add_argument("skill", help="Skill name (e.g. summarize_document)")

    sp_run = subs.add_parser(
        "run",
        help="Run a skill",
        description=(
            "Run a skill. Provide input as --field value pairs "
            "or as a JSON string via --json."
        ),
    )
    sp_run.add_argument("skill", help="Skill name (e.g. summarize_document)")
    sp_run.add_argument("--json", metavar="JSON", help="Full input as a JSON string")
    sp_run.add_argument("--json-output", action="store_true", help="Print raw JSON output")

    return parser


def main() -> None:
    parser = _build_parser()
    # parse_known_args so that unknown --field value args don't cause a parse error
    args, extra = parser.parse_known_args()

    if args.command == "list":
        cmd_list(args)
    elif args.command == "inspect":
        cmd_inspect(args)
    elif args.command == "run":
        cmd_run(args, extra)


if __name__ == "__main__":
    main()
