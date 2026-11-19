# ml/docint/ingest/__init__.py
"""VeriHire ingest package (Part 1: extraction, PII scrubbing, chunking).

Contract entry points (plan 7.2) re-exported at package level so the backend finds them as
``docint.ingest.run_ingest`` (added at integration, v1.2).
"""
from .extractor import ExtractionError
from .ingest import PIPELINE_VERSION, IngestResult, run_ingest

__all__ = ["ExtractionError", "IngestResult", "PIPELINE_VERSION", "run_ingest"]
