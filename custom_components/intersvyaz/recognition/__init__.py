"""Изолированный нейросетевой движок распознавания лиц Intersvyaz."""

from .engine import FaceRecognitionResult
from .dlib_engine import (
    DlibFaceRecognitionEngine,
    OpenCvFaceRecognitionEngine,
    PortableFaceRecognitionEngine,
)

__all__ = [
    "FaceRecognitionResult",
    "DlibFaceRecognitionEngine",
    "PortableFaceRecognitionEngine",
    "OpenCvFaceRecognitionEngine",
]
