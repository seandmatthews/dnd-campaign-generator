"""Shared router helpers: campaign resolution maps spine errors to 404.

The server rewrites every HTTPException/RequestValidationError body to
{"error": "..."} per the contract, so raising HTTPException(status, msg)
here is the whole story.
"""

from fastapi import HTTPException

import spine


def require_campaign(name: str):
    """Campaign dir or 404 (unknown or malformed name)."""
    try:
        return spine.require_campaign(name)
    except spine.CampaignNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except spine.CampaignError as e:
        raise HTTPException(status_code=404, detail=str(e))
