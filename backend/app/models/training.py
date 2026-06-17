"""
Training data models for LLM fine-tuning dataset generation.
"""

from pydantic import BaseModel


class OpenAIMessage(BaseModel):
    role: str
    content: str


class OpenAITrainingExample(BaseModel):
    messages: list[OpenAIMessage]


class AlpacaTrainingExample(BaseModel):
    instruction: str
    context: str = ""
    response: str
    input: str = ""


class RelationExtractionExample(BaseModel):
    instruction: str
    context: str
    response: str


class DatasetStats(BaseModel):
    total_samples: int
    avg_confidence: float
    avg_token_length: float
    strategy: str
    generation_time_seconds: float
    format: str
    path_length_distribution: dict = {}
