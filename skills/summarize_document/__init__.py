"""
Skill: summarize_document
Version: 0.1.0
Description: Produces a concise structured summary and key points from a document.

Public API:
    run(input: SummarizeDocumentInput) -> SummarizeDocumentOutput
"""
from .interfaces import SummarizeDocumentEvaluator, SummarizeDocumentInput, SummarizeDocumentOutput
from .core import run

__all__ = [
    "SummarizeDocumentInput",
    "SummarizeDocumentOutput",
    "SummarizeDocumentEvaluator",
    "run",
]
