"""Локальный изолированный движок распознавания лиц Intersvyaz."""

from .engine import FaceRecognitionResult
from .engine_v2 import OpenCvFaceRecognitionEngine, PortableFaceRecognitionEngine

__all__ = [
    "FaceRecognitionResult",
    "PortableFaceRecognitionEngine",
    "OpenCvFaceRecognitionEngine",
]
