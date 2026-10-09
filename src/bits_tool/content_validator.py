"""Content completeness: every printed word of the PDF body must appear in the XML (per-page coverage)."""
from .validators import content_coverage, validate_structure  # noqa: F401
