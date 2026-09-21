"""Absolute imports: one inside the corpus, one outside it."""

import json

from res_import_absolute.helpers import compute

RESULT = compute(21)
PAYLOAD = json.dumps({"result": RESULT})
