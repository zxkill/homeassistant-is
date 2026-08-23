"""Изолированный нейросетевой движок распознавания лиц Intersvyaz."""

from .engine import FaceRecognitionResult
from .engine_v2 import OpenCvFaceRecognitionEngine, PortableFaceRecognitionEngine
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
