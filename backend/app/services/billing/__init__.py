"""Billing provider selection, mirroring services/pipeline.py::get_provider."""

import logging

from ...config import get_settings
from .base import BillingProvider

logger = logging.getLogger(__name__)

_billing: BillingProvider | None = None


def get_billing() -> BillingProvider:
    global _billing
    if _billing is None:
        name = get_settings().resolved_billing_provider
        if name == "stripe":
            from .stripe_provider import StripeBillingProvider

            _billing = StripeBillingProvider()
        else:
            # Reached only when billing_provider is explicitly "mock". "auto"
            # never resolves here, so a misconfiguration cannot silently hand
            # out paid plans through a fake provider.
            from .mock import MockBillingProvider

            _billing = MockBillingProvider(get_settings().stripe_webhook_secret or "whsec_mock")
        logger.info("Billing provider: %s", _billing.name)
    return _billing


def reset_billing() -> None:
    """Test helper."""
    global _billing
    _billing = None
