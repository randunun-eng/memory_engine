"""Prompt template registry.

Loads templates from the filesystem and the database. The filesystem templates
are the source of truth for initial seeding; the database holds the active/shadow
state for runtime switching.

Hot-reload: call reload() to pick up new template files without restarting.
Phase 6 adds a file watcher for automatic reload.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Default template directory relative to the policy package
_DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A loaded, renderable prompt template."""

    site: str
    version: str
    template_text: str
    parameters: dict[str, dict[str, Any]]
    created_at: str | None = None

    def render(self, params: dict[str, Any]) -> str:
        """Render the template with Jinja2-style {{ var }} substitution.

        Uses simple string replacement, not full Jinja2, to avoid adding
        a dependency for what amounts to variable interpolation. Supports
        {{ var | default(value) }} for defaults.
        """
        text = self.template_text

        # Handle {{ var | default(value) }} patterns first
        def _replace_with_default(match: re.Match[str]) -> str:
            var_name = match.group(1).strip()
            default_val = match.group(2).strip()
            if var_name in params:
                return str(params[var_name])
            return default_val

        text = re.sub(
            r"\{\{\s*(\w+)\s*\|\s*default\(([^)]*)\)\s*\}\}",
            _replace_with_default,
            text,
        )

        # Handle plain {{ var }} substitution
        for key, value in params.items():
            text = text.replace("{{ " + key + " }}", str(value))
            text = text.replace("{{" + key + "}}", str(value))

        return text


@dataclass
class PromptRegistry:
    """In-memory registry of prompt templates.

    Templates are loaded from markdown files with YAML frontmatter.
    The registry tracks which template is active per site.
    """

    _templates: dict[str, dict[str, PromptTemplate]] = field(default_factory=dict)
    _active: dict[str, str] = field(default_factory=dict)  # site -> version

    def load_from_directory(self, template_dir: Path | None = None) -> int:
        """Load all templates from the given directory.

        Returns the number of templates loaded.
        """
        template_dir = template_dir or _DEFAULT_TEMPLATE_DIR
        count = 0

        for path in sorted(template_dir.glob("*.md")):
            if path.name == "README.md":
                continue
            try:
                template = _parse_template_file(path)
                self._register(template)
                count += 1
            except Exception:
                logger.warning("Failed to parse template %s", path, exc_info=True)

        logger.info("Loaded %d prompt templates from %s", count, template_dir)
        return count

    def reload(self, template_dir: Path | None = None) -> int:
        """Reload all templates. Returns count loaded."""
        self._templates.clear()
        self._active.clear()
        return self.load_from_directory(template_dir)

    def _register(self, template: PromptTemplate) -> None:
        """Register a template. First registered version per site becomes active."""
        if template.site not in self._templates:
            self._templates[template.site] = {}
        self._templates[template.site][template.version] = template

        # First version registered becomes active by default
        if template.site not in self._active:
            self._active[template.site] = template.version

    def get_active(self, site: str) -> PromptTemplate | None:
        """Get the active template for a site. Returns None if not found."""
        version = self._active.get(site)
        if version is None:
            return None
        return self._templates.get(site, {}).get(version)

    def get(self, site: str, version: str) -> PromptTemplate | None:
        """Get a specific template version."""
        return self._templates.get(site, {}).get(version)

    def set_active(self, site: str, version: str) -> None:
        """Promote a specific version to active for a site.

        Raises KeyError if the site/version combination doesn't exist.
        """
        if site not in self._templates or version not in self._templates[site]:
            msg = f"Template {site}:{version} not registered"
            raise KeyError(msg)
        self._active[site] = version

    @property
    def sites(self) -> list[str]:
        """All registered site names."""
        return sorted(self._templates.keys())


def _parse_template_file(path: Path) -> PromptTemplate:
    """Parse a markdown file with YAML frontmatter into a PromptTemplate."""
    text = path.read_text(encoding="utf-8")

    # Split frontmatter from body
    if not text.startswith("---"):
        msg = f"Template {path} missing YAML frontmatter"
        raise ValueError(msg)

    parts = text.split("---", maxsplit=2)
    if len(parts) < 3:
        msg = f"Template {path} has malformed frontmatter"
        raise ValueError(msg)

    frontmatter = yaml.safe_load(parts[1])
    body = parts[2].strip()

    return PromptTemplate(
        site=frontmatter["site"],
        version=frontmatter["version"],
        template_text=body,
        parameters=frontmatter.get("parameters", {}),
        created_at=frontmatter.get("created_at"),
    )
