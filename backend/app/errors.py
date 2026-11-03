"""Failures the rest of the pipeline can branch on.

Permanent errors are properties of the file. Retrying them will fail the same way,
so the task marks the document failed immediately.

Transient errors are properties of the surroundings (database, network, model API).
The Celery task retries those with exponential backoff.
"""

from __future__ import annotations


class UploadRejected(Exception):
    """The HTTP upload is invalid. Nothing is stored and nothing is queued."""


class PermanentIngestError(Exception):
    """The document itself cannot be ingested. Do not retry."""


class TransientIngestError(Exception):
    """Ingest failed for a reason that may disappear on a later attempt."""


class ProviderError(Exception):
    """A model or embedding call failed before it produced usable output."""


class ClaimNotFound(Exception):
    """The claim number in the URL does not exist yet."""
