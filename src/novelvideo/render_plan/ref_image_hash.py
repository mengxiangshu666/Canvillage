"""Compatibility exports for render-plan reference hashing.

The implementation lives in :mod:`novelvideo.utils.ref_image_hash` so render
planning and the other generation paths share one cache format and invalidation
policy.  This module remains import-compatible for integrations that used the
old render-plan path.
"""

from novelvideo.utils.ref_image_hash import RefImageHasher, file_sha256

# Private name kept for older integrations that imported it despite the leading
# underscore.  New code should use ``file_sha256``.
_sha256_of_file = file_sha256

__all__ = ["RefImageHasher", "file_sha256"]
