"""Локальный движок распознавания лиц Intersvyaz."""

from .engine import DlibFaceRecognitionEngine, FaceRecognitionResult

__all__ = ["DlibFaceRecognitionEngine", "FaceRecognitionResult"]
