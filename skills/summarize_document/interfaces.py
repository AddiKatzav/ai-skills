"""
Typed I/O contracts for the summarize_document skill.
All fields use strict Pydantic v2 validation. Fields marked sanitize=True
in json_schema_extra are masked by security.py before any LLM interaction.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_INPUT_CHARS = 16_000    # ~4 k tokens — generous for full documents
MAX_OUTPUT_CHARS = 3_000    # ~750 tokens — enough for a rich summary
MAX_KEY_POINTS = 10
MAX_KEY_POINT_CHARS = 200


class SummarizeDocumentInput(BaseModel):
    """
    Strict input contract. Pydantic raises ValidationError on any violation —
    the skill runner surfaces this as a 400-equivalent without reaching the LLM.
    """
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
    )

    text: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_INPUT_CHARS,
            description="The document text to summarize.",
            json_schema_extra={"sanitize": True},
        ),
    ]
    max_sentences: Annotated[
        int,
        Field(
            ge=1,
            le=20,
            description="Maximum number of sentences in the output summary.",
        ),
    ] = 5

    @field_validator("text", mode="before")
    @classmethod
    def text_must_not_be_whitespace_only(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must contain non-whitespace characters")
        return v

    @model_validator(mode="after")
    def validate_combined_length(self) -> "SummarizeDocumentInput":
        return self


class SummarizeDocumentOutput(BaseModel):
    """
    Guaranteed output contract. The LLM's raw response is parsed into this model.
    If parsing fails, core.py raises SkillExecutionError — the raw model string
    is NEVER returned to the caller.
    """
    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
    )

    result: Annotated[
        str,
        Field(
            min_length=1,
            max_length=MAX_OUTPUT_CHARS,
            description="Concise summary of the document.",
        ),
    ]
    key_points: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=MAX_KEY_POINT_CHARS)]],
        Field(
            min_length=1,
            max_length=MAX_KEY_POINTS,
            description="Key takeaways extracted from the document.",
        ),
    ]
    confidence: Annotated[
        float,
        Field(ge=0.0, le=1.0, description="Model self-reported confidence [0, 1]."),
    ] = 1.0
    truncated: bool = Field(
        default=False,
        description="True if the document was too long to fully process.",
    )

    @field_validator("result", mode="after")
    @classmethod
    def result_must_not_contain_raw_system_prompt(cls, v: str) -> str:
        forbidden_fragments = [
            "You are a helpful assistant",
            "<|system|>",
            "[INST]",
            "<<SYS>>",
        ]
        lower = v.lower()
        for fragment in forbidden_fragments:
            if fragment.lower() in lower:
                raise ValueError(
                    f"Output contains forbidden system-prompt fragment: {fragment!r}"
                )
        return v


class SummarizeDocumentEvaluator:
    """
    Deterministic keyword-overlap scorer for summarize_document output.

    Scoring strategy: weighted keyword recall over expected_keywords from the
    reference fixture, scaled by model confidence and penalized for truncation.
    CI gate: average score >= 0.85 across the committed reference fixture set.
    """

    def score(
        self,
        output: SummarizeDocumentOutput,
        reference: dict[str, Any],
    ) -> float:
        """
        Compute a deterministic quality score.

        Args:
            output:    The skill's actual output.
            reference: Expected values from the fixture. Keys:
                       - "expected_keywords": list[str] — terms that must appear
                         in result or key_points (case-insensitive).

        Returns:
            Float in [0.0, 1.0]. >= 0.85 is the passing threshold.
        """
        expected_keywords: list[str] = reference.get("expected_keywords", [])

        if not expected_keywords:
            return 1.0 if (output.result and output.key_points) else 0.0

        combined_text = (
            output.result.lower() + " " + " ".join(output.key_points).lower()
        )
        matched = sum(1 for kw in expected_keywords if kw.lower() in combined_text)
        keyword_score = matched / len(expected_keywords)

        # Scale by confidence: full credit requires both keyword match and high confidence
        confidence_factor = 0.7 + 0.3 * output.confidence

        # Minor penalty when the model had to truncate — completeness is a quality signal
        truncation_penalty = 0.9 if output.truncated else 1.0

        return min(1.0, keyword_score * confidence_factor * truncation_penalty)
