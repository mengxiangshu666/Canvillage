"""Compatibility import for the shared video request contract implementation.

Alias the module itself so legacy callers and monkeypatches still share the
same classes, functions and globals as the service implementation.
"""

import sys

from novelvideo.services import _video_request_contract as _implementation

sys.modules[__name__] = _implementation
