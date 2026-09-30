"""Input checks shared by the auth middleware and the payment routes."""

import re
from uuid import UUID

# EC-7: only the canonical 8-4-4-4-12 form; uuid.UUID() alone also accepts urn:, braces, etc.
CANONICAL_UUID = re.compile(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def parse_canonical_uuid(value: object) -> UUID | None:
    """The UUID, or None if the value is not a string in the canonical form."""
    if not isinstance(value, str) or not CANONICAL_UUID.fullmatch(value):
        return None
    return UUID(value)
