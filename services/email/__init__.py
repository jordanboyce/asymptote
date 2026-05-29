"""Outbound email delivery (Resend).

Shared transport for transactional mail — issue reports (services.feedback)
and the weekly advisor digest (services.digest). Pure POST to Resend's REST
API via httpx; no SDK dependency.
"""

from services.email.sender import EmailResult, send_email

__all__ = ["EmailResult", "send_email"]
