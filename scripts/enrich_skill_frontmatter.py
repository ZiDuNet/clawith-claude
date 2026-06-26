#!/usr/bin/env python3
"""Restore metadata (category, icon) into the frontmatter of already-imported skills.

The initial import (`scripts/import_claude_skills.py`) stripped unknown frontmatter
fields into a JSON comment for compatibility with Clawith's parser. This script
re-introduces a proper YAML mapping under `metadata:` so the auto-discover
function in skill_seeder.py can categorize skills.

Run after the initial import:
    python scripts/enrich_skill_frontmatter.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except ImportError:
    print("ERROR: PyYAML required (pip install pyyaml)", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = ROOT / "backend" / "agent_template" / "builtin_skills"

# Match the <!-- Upstream frontmatter ... --> block produced by import_claude_skills.py
UPSTREAM_BLOCK_RE = re.compile(
    r"<!--\s*Upstream frontmatter \(preserved for reference, not parsed\):\s*\n(.*?)-->",
    re.DOTALL,
)
# Match the source provenance comment (Domain: ...)
DOMAIN_RE = re.compile(r"Domain\s*:\s*(\S+)")

# Default icon per category (emoji)
CATEGORY_ICONS = {
    "engineering": "🛠️", "marketing": "📣", "product": "📦",
    "finance": "💰", "research": "🔬", "c-level": "👔",
    "compliance": "⚖️", "commercial": "💼", "operations": "⚙️",
    "productivity": "⏱️", "orchestration": "🧭",
}
DEFAULT_ICON = "📚"


def enrich_one(skill_md: Path) -> bool:
    text = skill_md.read_text(encoding="utf-8", errors="replace")

    # Already enriched? Skip.
    if "\nmetadata:\n" in text or re.search(r"^metadata:\s*$", text, re.MULTILINE):
        return False

    # Pull original metadata from the preserved block
    m = UPSTREAM_BLOCK_RE.search(text)
    if not m:
        return False
    raw_meta = m.group(1).strip()
    try:
        meta = yaml.safe_load(raw_meta)
    except yaml.YAMLError:
        meta = None
    if not isinstance(meta, dict):
        return False

    # Inherit `category` and `icon` from the original metadata block
    category = meta.get("category") if isinstance(meta.get("category"), str) else None
    icon = meta.get("icon") if isinstance(meta.get("icon"), str) else None

    # Fallback: read domain from the provenance comment
    if not category:
        domain_match = DOMAIN_RE.search(text)
        if domain_match:
            category = domain_match.group(1)
            # Map known domains → friendly category
            domain_map = {
                "engineering": "engineering", "engineering-team": "engineering",
                "marketing": "marketing", "marketing-skill": "marketing",
                "c-level-advisor": "c-level", "product-team": "product",
                "finance": "finance", "research": "research",
                "research-ops": "research", "ra-qm-team": "compliance",
                "compliance-os": "compliance", "commercial": "commercial",
                "business-growth": "operations", "business-operations": "operations",
                "productivity": "productivity", "markdown-html": "productivity",
                "project-management": "operations",
            }
            category = domain_map.get(category, category)

    if not icon:
        icon = CATEGORY_ICONS.get(category or "", DEFAULT_ICON)

    # Inject `metadata:` mapping right after the description line
    inject = f"metadata:\n  category: \"{category or 'general'}\"\n  icon: \"{icon}\"\n"
    new_text = re.sub(
        r'(description: "[^"]*"\n)',
        r"\1" + inject,
        text,
        count=1,
    )
    if new_text == text:
        return False
    skill_md.write_text(new_text, encoding="utf-8")
    return True


def main() -> int:
    if not SKILLS_DIR.is_dir():
        print(f"ERROR: not found: {SKILLS_DIR}", file=sys.stderr)
        return 2

    enriched = 0
    skipped = 0
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            if enrich_one(skill_md):
                enriched += 1
            else:
                skipped += 1
        except Exception as exc:
            print(f"  ! {skill_dir.name}: {exc}", file=sys.stderr)

    print(f"Enriched: {enriched}  Skipped/already-done: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())