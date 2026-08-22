"""Неблокирующие unit-тесты математической части recognition engine."""

import pytest

from custom_components.intersvyaz.recognition.engine import DlibFaceRecognitionEngine


def test_euclidean_distance() -> None:
    distance = DlibFaceRecognitionEngine._euclidean_distance([0.0, 0.0], [3.0, 4.0])
    assert distance == pytest.approx(5.0)


def test_euclidean_distance_rejects_different_lengths() -> None:
    distance = DlibFaceRecognitionEngine._euclidean_distance([0.0], [0.0, 1.0])
    assert distance == float("inf")
