"""Локальный изолированный движок распознавания лиц Intersvyaz."""

from .engine import (
    FaceRecognitionResult,
    OpenCvFaceRecognitionEngine,
    PortableFaceRecognitionEngine,
)

__all__ = [
    "FaceRecognitionResult",
    "PortableFaceRecognitionEngine",
    "OpenCvFaceRecognitionEngine",
]
