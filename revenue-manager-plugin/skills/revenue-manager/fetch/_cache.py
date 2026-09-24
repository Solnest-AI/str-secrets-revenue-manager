"""Where the reducers keep raw API payloads.

Runtime caches do not belong inside the plugin tree: `claude plugin update` copies the
whole directory into ~/.claude/plugins/cache/, so a payload cached beside the script gets
duplicated on every version bump. Everything lands under one user-level directory instead.

Resolution order:
  1. $RC_CACHE_DIR                       (tests, or an operator who wants it elsewhere)
  2. $XDG_CACHE_HOME/revenue-manager
  3. ~/.cache/revenue-manager
"""
import hashlib
import json
import os
import tempfile


def cache_dir(sub: str = "") -> str:
    root = os.environ.get("RC_CACHE_DIR")
    if not root:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        root = os.path.join(base, "revenue-manager")
    path = os.path.join(root, sub) if sub else root
    os.makedirs(path, exist_ok=True)
    return path


def cache_name(prefix: str, *identity) -> str:
    """A filename scoped to the complete request, never a truncated listing id."""
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}.json"


def listing_matches(blob, listing: str, pms: str, window=None) -> bool:
    """A filename is not evidence that the payload belongs to the requested listing."""
    return (isinstance(blob, dict) and blob.get("listing") == listing
            and blob.get("pms") == pms
            and (window is None or blob.get("window") == list(window)))


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, value):
    """Publish one complete cache file, with an independent temp file per caller."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".cache-", suffix=".tmp", delete=False) as handle:
            temp_path = handle.name
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
