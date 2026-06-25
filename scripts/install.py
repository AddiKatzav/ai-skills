#!/usr/bin/env python3
"""
Install ai-skills into a target repository as Claude Code slash commands.

For each skill in this repo, creates a .claude/commands/<skill-name>.md file
in the target repo. Users can then invoke skills directly from Claude Code:

    /summarize-document Summarize this document: <paste text>
    /run-skill summarize_document --text "..."

Usage:
    python scripts/install.py /path/to/target/repo
    python scripts/install.py /path/to/target/repo --skills summarize_document
    python scripts/install.py /path/to/target/repo --dry-run

Options:
    --skills NAME [NAME ...]  Install only the listed skills (default: all)
    --dry-run                 Print what would be created without writing files
    --force                   Overwrite existing command files
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
APPLY_PY   = REPO_ROOT / "apply.py"


# ─────────────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────────────

def _discover_skills() -> dict[str, dict]:
    skills: dict[str, dict] = {}
    for path in sorted(SKILLS_DIR.iterdir()):
        meta_file = path / "metadata.json"
        if path.is_dir() and not path.name.startswith("_") and meta_file.exists():
            try:
                with meta_file.open(encoding="utf-8") as fh:
                    skills[path.name] = json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
    return skills


def _die(msg: str, code: int = 1) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


# ─────────────────────────────────────────────────────────────────────────────
# Slash-command content generation
# ─────────────────────────────────────────────────────────────────────────────

def _field_examples(fields: list[dict]) -> str:
    """Build example --flag usage from metadata input_fields."""
    parts = []
    for f in fields:
        name = f["name"].replace("_", "-")
        ftype = f.get("type", "str")
        if ftype == "str":
            parts.append(f'--{name} "your text here"')
        elif ftype == "int":
            default = f.get("default", 5)
            parts.append(f"--{name} {default}")
        else:
            parts.append(f"--{name} <value>")
    return " ".join(parts[:3])  # at most 3 examples


def _per_skill_command(skill_id: str, meta: dict) -> str:
    """
    Generate the .md content for a per-skill slash command.
    The $ARGUMENTS placeholder is filled in by Claude Code at invocation time.
    """
    display  = meta.get("display_name", skill_id)
    desc     = meta.get("description", "")
    fields   = meta.get("interface", {}).get("input_fields", [])
    out_flds = meta.get("interface", {}).get("output_fields", [])
    security = meta.get("security", {})
    ex       = _field_examples(fields)
    kdb_name = skill_id.replace("_", "-")
    primary  = fields[0]["name"] if fields else "text"

    parts: list[str] = []

    parts.append(f"# /{kdb_name}\n")
    parts.append(f"Run the **{display}** skill from the ai-skills repository.\n")
    parts.append(f"> {desc}\n")

    if fields:
        rows = "\n".join(
            f"| `{f['name']}` | `{f['type']}` | {'yes' if f.get('required') else 'no'} |"
            for f in fields
        )
        parts.append(
            "**Input fields:**\n\n"
            "| Field | Type | Required |\n"
            "|-------|------|----------|\n"
            + rows + "\n"
        )

    if out_flds:
        rows = "\n".join(f"| `{f['name']}` | `{f['type']}` |" for f in out_flds)
        parts.append(
            "**Output fields:**\n\n"
            "| Field | Type |\n"
            "|-------|------|\n"
            + rows + "\n"
        )

    threats = ", ".join(security.get("active_threats", []))
    if threats:
        parts.append(f"Security: mitigates threats {threats}.\n")

    parts.append("---\n")
    parts.append("## How to use this command\n")
    parts.append(f"The user has typed:\n\n```\n/{kdb_name} $ARGUMENTS\n```\n")

    parts.append(
        "Follow these steps:\n\n"
        f"1. **Parse `$ARGUMENTS`** to extract the input field values.\n"
        f"   - If the argument looks like a JSON object (`{{...}}`), use it with `--json`.\n"
        f"   - Otherwise treat the text as the primary input field (`{primary}`).\n"
        f"     Any `--field value` flags in the argument override individual fields.\n\n"
        f"2. **Run the skill** using the Bash tool:\n\n"
        f"   ```bash\n"
        f"   python3 {APPLY_PY} run {skill_id} {ex}\n"
        f"   ```\n\n"
        f"   For multi-line text, use `--json`:\n\n"
        f"   ```bash\n"
        f'   python3 {APPLY_PY} run {skill_id} --json \'{{"text": "<user text>"}}\'\n'
        f"   ```\n\n"
        f"3. **Present the result** to the user in a readable format.\n"
        f"   - Show `result` as a prose paragraph.\n"
        f"   - Show `key_points` as a bullet list (if present).\n"
        f"   - Show `confidence` as a percentage.\n"
        f"   - If `truncated` is true, note that the input was partially processed.\n\n"
        f"4. If the command fails (no API key, validation error, injection detected):\n"
        f"   - Explain the error clearly.\n"
        f"   - Suggest the user set `ANTHROPIC_API_KEY` in their shell if missing.\n"
    )

    return "\n".join(parts)


def _general_dispatcher_command(skills: dict[str, dict]) -> str:
    """Generate the general /run-skill dispatcher command."""
    skill_list = "\n".join(
        f"- `{sid}` — {m.get('description', '')[:60]}"
        for sid, m in skills.items()
    )

    return textwrap.dedent(f"""\
        # /run-skill

        Run any skill from the ai-skills repository.

        **Available skills:**

        {skill_list}

        ---

        ## How to use this command

        The user has typed:

        ```
        /run-skill $ARGUMENTS
        ```

        **`$ARGUMENTS` format:** `<skill_name> [--field value ...]`

        Example: `/run-skill summarize_document --text "My document..."`

        Follow these steps:

        1. Parse the first word of `$ARGUMENTS` as the skill name.
        2. Parse remaining tokens as `--field value` pairs.
        3. Run the skill using the Bash tool:

        ```bash
        python3 {APPLY_PY} run <skill_name> [--field value ...]
        ```

        Or with JSON input:

        ```bash
        python3 {APPLY_PY} run <skill_name> --json '{{"field": "value"}}'
        ```

        4. If the user didn't specify a skill, run:

        ```bash
        python3 {APPLY_PY} list
        ```

        and ask the user to pick one.

        5. Present the result clearly (see per-skill command files for output format guidance).
    """)


# ─────────────────────────────────────────────────────────────────────────────
# Installation
# ─────────────────────────────────────────────────────────────────────────────

def _install(
    target_repo: Path,
    skills: dict[str, dict],
    *,
    dry_run: bool,
    force: bool,
) -> None:
    commands_dir = target_repo / ".claude" / "commands"

    if dry_run:
        print(f"[dry-run] Would create directory: {commands_dir}")
    else:
        commands_dir.mkdir(parents=True, exist_ok=True)

    files_written: list[str] = []
    files_skipped: list[str] = []

    # Per-skill command files
    for skill_id, meta in skills.items():
        fname = skill_id.replace("_", "-") + ".md"
        dest  = commands_dir / fname
        content = _per_skill_command(skill_id, meta)

        if dest.exists() and not force:
            files_skipped.append(str(dest))
            continue

        if dry_run:
            print(f"[dry-run] Would write: {dest}")
        else:
            dest.write_text(content, encoding="utf-8")
            files_written.append(str(dest))

    # General dispatcher
    dispatcher_dest = commands_dir / "run-skill.md"
    if dispatcher_dest.exists() and not force:
        files_skipped.append(str(dispatcher_dest))
    else:
        dispatcher_content = _general_dispatcher_command(skills)
        if dry_run:
            print(f"[dry-run] Would write: {dispatcher_dest}")
        else:
            dispatcher_dest.write_text(dispatcher_content, encoding="utf-8")
            files_written.append(str(dispatcher_dest))

    # Summary
    if not dry_run:
        print(f"\nInstalled {len(files_written)} command(s) into {commands_dir}")
        for f in files_written:
            print(f"  ✓  {Path(f).name}")
        if files_skipped:
            print(f"\nSkipped {len(files_skipped)} existing file(s) (use --force to overwrite):")
            for f in files_skipped:
                print(f"  –  {Path(f).name}")

        _print_setup_instructions(target_repo, skills)


def _print_setup_instructions(target_repo: Path, skills: dict[str, dict]) -> None:
    example_skill = next(iter(skills))
    example_cmd   = example_skill.replace("_", "-")

    print(textwrap.dedent(f"""
    ─────────────────────────────────────────────────────
    Setup complete. Next steps:

    1. Set your Anthropic API key (once, in your shell profile):
       export ANTHROPIC_API_KEY=sk-ant-...

    2. Open a Claude Code session in {target_repo}

    3. Use skills with slash commands:
       /{example_cmd} Paste your document text here...
       /run-skill {example_skill} --text "..."

    4. Or call the CLI directly from anywhere:
       python3 {APPLY_PY} run {example_skill} --text "..."
       python3 {APPLY_PY} list
    ─────────────────────────────────────────────────────
    """))


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install ai-skills slash commands into a target repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python scripts/install.py /path/to/my-project
              python scripts/install.py /path/to/my-project --skills summarize_document
              python scripts/install.py /path/to/my-project --dry-run
              python scripts/install.py /path/to/my-project --force
        """),
    )
    parser.add_argument("repo", metavar="REPO_PATH", help="Path to the target repository")
    parser.add_argument(
        "--skills", nargs="+", metavar="SKILL",
        help="Install only these skills (default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing")
    parser.add_argument("--force", action="store_true", help="Overwrite existing command files")
    args = parser.parse_args()

    target = Path(args.repo).resolve()
    if not target.exists():
        _die(f"Target path does not exist: {target}")
    if not target.is_dir():
        _die(f"Target path is not a directory: {target}")

    all_skills = _discover_skills()
    if not all_skills:
        _die(f"No skills found in {SKILLS_DIR}")

    if args.skills:
        missing = [s for s in args.skills if s not in all_skills]
        if missing:
            _die(
                f"Unknown skill(s): {', '.join(missing)}\n"
                f"Available: {', '.join(all_skills)}"
            )
        selected = {k: all_skills[k] for k in args.skills}
    else:
        selected = all_skills

    print(f"Installing {len(selected)} skill(s) → {target}/.claude/commands/")
    for sid in selected:
        print(f"  • {sid}  (v{selected[sid].get('version', '?')})")
    print()

    _install(target, selected, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
