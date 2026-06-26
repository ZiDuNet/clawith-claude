#!/usr/bin/env python3
"""Import skills from alirezarezvani/claude-skills into Clawith format.

Walks the source tree, finds every SKILL.md, copies the skill folder
(SKILL.md + scripts/ + references/ + assets/) into the Clawith skill layout,
adapts frontmatter (description truncation, comment-out unknown fields),
and handles name collisions across domains by prefixing the domain.

Output layout (matches docs/SKILL_FORMAT.md §2 Format A):

    <output>/<skill-name>/
        ├── SKILL.md
        ├── scripts/...
        ├── references/...
        └── assets/... (or examples/...)

Usage:
    python scripts/import_claude_skills.py
    python scripts/import_claude_skills.py --source ../claude-skills --output backend/agent_template/builtin_skills
    python scripts/import_claude_skills.py --dry-run
    python scripts/import_claude_skills.py --force   # overwrite existing
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "claude-skills"
DEFAULT_OUTPUT = REPO_ROOT / "backend" / "agent_template" / "builtin_skills"
MAX_DESCRIPTION_LEN = 200

# Subfolders inside a skill directory that get carried over (source → dest name).
AUX_FOLDERS = {
    "scripts": "scripts",
    "references": "references",
    "assets": "assets",       # could be renamed to examples/ but assets is the upstream convention
    "examples": "examples",
}

# Top-level repo paths to skip entirely (repo-internal, not for distribution).
SKIP_DIR_NAMES = {
    "tests", "documentation", "megaprompts", "audit",
    ".github", ".git", ".codex", ".gemini", ".hermes", ".vibe", ".claude", ".claude-plugin",
    "docs", "scripts", "templates", "standards", "agents", "commands", "shared", "assets", "blog", "changelog", "hack",
    "helm", "install", "manager", "worker", "migrate", "plugins", "copaw", "openclaw-base", "openhuman", "hiclaw-controller", "hermes",
}

# Domain roots we care about (the actual skill packs in claude-skills).
SKILL_DOMAINS = {
    "engineering", "engineering-team", "product-team", "marketing", "marketing-skill",
    "c-level-advisor", "project-management", "ra-qm-team", "compliance-os",
    "business-growth", "business-operations", "commercial", "finance",
    "research", "research-ops", "productivity", "markdown-html",
}


@dataclass
class SkillRecord:
    """One skill found in the source tree."""
    domain: str                  # top-level domain folder, e.g. 'engineering'
    raw_name: str                # skill folder name on disk, e.g. 'docker-development'
    skill_dir: Path              # directory containing SKILL.md
    frontmatter: dict = field(default_factory=dict)
    description: str = ""
    name: str = ""

    @property
    def output_name(self) -> str:
        """Final folder name in Clawith layout (collision-safe)."""
        # Filled in by the registry after collision resolution.
        return getattr(self, "_output_name", self.raw_name)

    @output_name.setter
    def output_name(self, value: str) -> None:
        self._output_name = value


@dataclass
class ImportReport:
    total_found: int = 0
    converted: int = 0
    skipped_existing: int = 0
    skipped_no_skill_md: int = 0
    errors: list[str] = field(default_factory=list)
    by_domain: dict[str, int] = field(default_factory=dict)
    collisions: list[tuple[str, list[str]]] = field(default_factory=list)

    def print(self) -> None:
        print("\n" + "=" * 60)
        print(f"Claude-Skills → Clawith Import Report")
        print("=" * 60)
        print(f"  SKILL.md files found : {self.total_found}")
        print(f"  Converted            : {self.converted}")
        print(f"  Skipped (existing)   : {self.skipped_existing}")
        print(f"  Skipped (no SKILL.md): {self.skipped_no_skill_md}")
        print(f"  Errors               : {len(self.errors)}")
        print()
        print(f"  Per-domain breakdown:")
        for d in sorted(self.by_domain):
            print(f"    {d:<25} {self.by_domain[d]:>3} skills")
        if self.collisions:
            print()
            print(f"  Name collisions resolved (prefixed with domain):")
            for name, domains in self.collisions:
                print(f"    {name}  ←  {', '.join(domains)}")
        if self.errors:
            print()
            print(f"  Errors:")
            for e in self.errors[:20]:
                print(f"    ! {e}")
            if len(self.errors) > 20:
                print(f"    ... and {len(self.errors) - 20} more")
        print("=" * 60)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and body from a markdown file.

    Returns (frontmatter_dict, body_text). Frontmatter is empty dict if absent.
    Works with or without PyYAML — falls back to a simple key:value parser.
    """
    if not text.startswith("---"):
        return {}, text
    # Find the closing fence
    end_match = re.search(r"\n---\s*(?:\n|$)", text[3:])
    if not end_match:
        return {}, text
    fm_block = text[3 : 3 + end_match.start()]
    body = text[3 + end_match.end():]
    fm_block = fm_block.strip()

    if _HAS_YAML:
        try:
            parsed = yaml.safe_load(fm_block) or {}
            if isinstance(parsed, dict):
                return parsed, body.lstrip("\n")
        except yaml.YAMLError:
            pass

    # Fallback: parse simple key: value lines (no nested structures)
    parsed: dict = {}
    for line in fm_block.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"').strip("'")
        if value:
            parsed[key.strip()] = value
    return parsed, body.lstrip("\n")


def truncate_description(desc: str, limit: int = MAX_DESCRIPTION_LEN) -> str:
    """Truncate at the last word boundary within `limit` chars. Append '…' if cut."""
    if not desc or len(desc) <= limit:
        return desc.strip()
    cut = desc[:limit].rsplit(" ", 1)[0]
    return (cut + "…").strip()


def find_skills(source: Path) -> list[SkillRecord]:
    """Walk the source tree and return every SKILL.md as a SkillRecord.

    Handles four source layouts seen in claude-skills:
      (a) <domain>/<skill>/skills/<skill>/SKILL.md             ← per-skill-folder
      (b) <domain>/skills/<skill>/SKILL.md                     ← flat-in-domain-skills
      (c) <domain>/<skill>/SKILL.md                            ← flat-in-domain (rare)
      (d) <domain>/<parent>/skills/<sub-skill>/SKILL.md        ← sub-skill only (no parent SKILL.md)
                                                              e.g. playwright-pro / self-improving-agent
    """
    records: list[SkillRecord] = []
    if not source.is_dir():
        return records

    for domain_dir in sorted(source.iterdir()):
        if not domain_dir.is_dir():
            continue
        if domain_dir.name not in SKILL_DOMAINS:
            continue

        # Layout (b): <domain>/skills/<skill>/SKILL.md
        skills_root = domain_dir / "skills"
        if skills_root.is_dir():
            for sub in sorted(skills_root.iterdir()):
                if not sub.is_dir():
                    continue
                skill_md = sub / "SKILL.md"
                if skill_md.is_file():
                    rec = _make_record(domain_dir.name, sub.name, sub, skill_md)
                    if rec:
                        records.append(rec)

        # Layouts (a), (c), (d): <domain>/<skill>/* and possibly nested skills/<sub>/SKILL.md
        for skill_dir in sorted(domain_dir.iterdir()):
            if not skill_dir.is_dir() or skill_dir.name == "skills":
                continue
            # Layout (a): <domain>/<skill>/skills/<skill>/SKILL.md
            nested = skill_dir / "skills" / skill_dir.name
            skill_md = nested / "SKILL.md"
            if skill_md.is_file():
                rec = _make_record(domain_dir.name, skill_dir.name, nested, skill_md)
                if rec:
                    records.append(rec)
                # Don't `continue` — also import any nested sub-skills.
                # The parent's SKILL.md is usually an orchestrator/index that
                # references sub-skills; importing both gives Clawith's agent
                # the full menu via progressive disclosure.

            # Layout (c): <domain>/<skill>/SKILL.md (no nested skills dir)
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                rec = _make_record(domain_dir.name, skill_dir.name, skill_dir, skill_md)
                if rec:
                    records.append(rec)

            # Layout (d): no parent SKILL.md — each <domain>/<parent>/skills/<sub>/SKILL.md
            # is its own importable skill, named "<parent>-<sub>".
            # Also runs even when layout (a)/(c) matched, to pull in sub-skill companions.
            nested_skills = skill_dir / "skills"
            if nested_skills.is_dir():
                for sub in sorted(nested_skills.iterdir()):
                    if not sub.is_dir():
                        continue
                    sub_md = sub / "SKILL.md"
                    if sub_md.is_file():
                        # Avoid "agenthub-agenthub" duplication when sub.name == skill_dir.name
                        if sub.name == skill_dir.name:
                            composed_name = sub.name
                        else:
                            composed_name = f"{skill_dir.name}-{sub.name}"
                        rec = _make_record(domain_dir.name, composed_name, sub, sub_md)
                        if rec:
                            records.append(rec)

    return records


def _make_record(domain: str, raw_name: str, skill_dir: Path, skill_md: Path) -> SkillRecord | None:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"  ! read failed: {skill_md}: {exc}", file=sys.stderr)
        return None
    fm, _ = parse_frontmatter(text)
    if not fm:
        # No frontmatter — still import but flag it
        return SkillRecord(domain=domain, raw_name=raw_name, skill_dir=skill_dir,
                           frontmatter={}, name=raw_name, description="")
    name = str(fm.get("name", "")).strip().strip('"').strip("'") or raw_name
    desc = str(fm.get("description", "")).strip().strip('"').strip("'")
    return SkillRecord(domain=domain, raw_name=raw_name, skill_dir=skill_dir,
                       frontmatter=fm, name=name, description=desc)


def resolve_collisions(records: list[SkillRecord]) -> list[tuple[str, list[str]]]:
    """If two skills share the same output_name, prefix the second+ with their domain.

    Also dedupes records that point at the same source file (layout (a) and
    layout (d) can both pick up `domain/skill/skills/skill/SKILL.md` when
    `sub.name == skill_dir.name`).
    """
    # Dedupe by absolute path of source SKILL.md — first wins
    seen_paths: set[Path] = set()
    unique: list[SkillRecord] = []
    for rec in records:
        skill_md = rec.skill_dir / "SKILL.md"
        if skill_md in seen_paths:
            continue
        seen_paths.add(skill_md)
        rec.output_name = rec.raw_name
        unique.append(rec)
    records[:] = unique

    by_name: dict[str, list[SkillRecord]] = {}
    for rec in records:
        by_name.setdefault(rec.output_name, []).append(rec)
    collisions: list[tuple[str, list[str]]] = []
    for name, group in by_name.items():
        if len(group) > 1:
            collisions.append((name, sorted({r.domain for r in group})))
            for rec in group:
                rec.output_name = f"{rec.domain}-{rec.raw_name}"
    return collisions


def write_skill(rec: SkillRecord, output_root: Path, force: bool, dry_run: bool) -> tuple[bool, str]:
    """Copy one skill to the output tree. Returns (wrote_ok, status_msg)."""
    dest = output_root / rec.output_name
    if dest.exists() and not force:
        return False, "skip-existing"

    if dry_run:
        return True, "dry-run"

    dest.mkdir(parents=True, exist_ok=True)

    # Write the SKILL.md with adapted frontmatter
    src_md = rec.skill_dir / "SKILL.md"
    if not src_md.is_file():
        src_md = rec.skill_dir / "skill.md"
    text = src_md.read_text(encoding="utf-8", errors="replace")
    adapted = _adapt_skill_md(text, rec)
    (dest / "SKILL.md").write_text(adapted, encoding="utf-8")

    # Copy auxiliary folders
    for src_sub, dest_sub in AUX_FOLDERS.items():
        src_dir = rec.skill_dir / src_sub
        if src_dir.is_dir():
            shutil.copytree(src_dir, dest / dest_sub, dirs_exist_ok=True)

    return True, "ok"


def _adapt_skill_md(text: str, rec: SkillRecord) -> str:
    """Rewrite frontmatter to fit Clawith's parser (name + description only).

    - Truncate description to 200 chars
    - Move unknown fields into a HTML comment block right after the frontmatter
    - Add a leading comment crediting the upstream license + source path
    """
    fm, body = parse_frontmatter(text)
    name = rec.name or rec.raw_name
    desc = truncate_description(rec.description or fm.get("description", ""))
    license_info = str(fm.get("license", "")).strip()
    # Strip quotation marks Clawith's parser doesn't like in description
    safe_desc = desc.replace("\n", " ").replace("\r", " ").strip()

    # Unknown frontmatter keys (we keep name + description; license gets a comment)
    KNOWN = {"name", "description"}
    unknown = {k: v for k, v in fm.items() if k not in KNOWN}

    parts: list[str] = []
    # Provenance comment (kept in the file, ignored by Clawith parser)
    provenance = (
        f"<!--\n"
        f"  Source    : claude-skills / {rec.domain}/{rec.raw_name}/SKILL.md\n"
        f"  License   : {license_info or 'MIT (upstream)'}\n"
        f"  Domain    : {rec.domain}\n"
        f"  Adapted   : imported via scripts/import_claude_skills.py\n"
        f"-->\n"
    )
    parts.append(provenance)

    # Frontmatter — only name + description (Clawith parses these)
    parts.append("---")
    parts.append(f'name: "{name}"')
    parts.append(f'description: "{safe_desc}"')
    parts.append("---")
    parts.append("")

    # Stash unknown fields as a metadata comment for human readers
    if unknown:
        import json
        try:
            meta_str = json.dumps(unknown, ensure_ascii=False, default=str, indent=2)
        except Exception:
            meta_str = repr(unknown)
        parts.append("<!-- Upstream frontmatter (preserved for reference, not parsed):")
        for line in meta_str.splitlines():
            parts.append(f"     {line}")
        parts.append("-->")
        parts.append("")

    # Body — keep as-is. Clawith puts the whole SKILL.md into the agent's
    # workspace and reads it on demand.
    parts.append(body.lstrip("\n").rstrip() + "\n")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import claude-skills into Clawith format.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                        help=f"claude-skills checkout (default: {DEFAULT_SOURCE})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Clawith skill output dir (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Walk and report without writing files")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing skill folders")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    source: Path = args.source.resolve()
    output: Path = args.output.resolve()

    if not source.is_dir():
        print(f"ERROR: source not found: {source}", file=sys.stderr)
        return 2

    print(f"Source : {source}")
    print(f"Output : {output}")
    print(f"Mode   : {'DRY-RUN' if args.dry_run else 'force' if args.force else 'safe (skip existing)'}")

    records = find_skills(source)
    report = ImportReport(total_found=len(records))

    if not records:
        print("No SKILL.md files found in known domains.")
        report.print()
        return 1

    collisions = resolve_collisions(records)
    report.collisions = collisions

    if not args.dry_run:
        output.mkdir(parents=True, exist_ok=True)

    for rec in records:
        ok, status = write_skill(rec, output, force=args.force, dry_run=args.dry_run)
        if status == "skip-existing":
            report.skipped_existing += 1
            if args.verbose:
                print(f"  · skip (exists) {rec.output_name}")
        elif status in ("ok", "dry-run"):
            report.converted += 1
            report.by_domain[rec.domain] = report.by_domain.get(rec.domain, 0) + 1
            if args.verbose:
                print(f"  + {rec.output_name}  ←  {rec.domain}/{rec.raw_name}")
        else:
            report.errors.append(f"{rec.domain}/{rec.raw_name}: {status}")

    report.print()
    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())