"""Изолированный локальный движок распознавания лиц Intersvyaz.

Нативный ``dlib`` намеренно НЕ импортируется в процессе Home Assistant.
Он запускается в отдельном worker-процессе. Это важно для стабильности:
если бинарный wheel dlib несовместим с инструкциями старого CPU и завершается
SIGILL/SIGSEGV, падает только worker, а Home Assistant продолжает работать.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import logging
import select
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger("custom_components.intersvyaz.recognition")

_WORKER_TIMEOUT_SECONDS = 45.0
_FATAL_SIGNALS = {
    getattr(signal, "SIGILL", 4),
    getattr(signal, "SIGSEGV", 11),
    getattr(signal, "SIGBUS", 7),
}


@dataclass(frozen=True)
class FaceRecognitionResult:
    """Результат анализа одного кадра."""

    faces_detected: int
    matched_name: str | None = None
    distance: float | None = None

    @property
    def matched(self) -> bool:
        """Есть ли совпадение с известным лицом."""

        return self.matched_name is not None


class DlibFaceRecognitionEngine:
    """Клиент изолированного dlib worker-процесса."""

    def __init__(self, *, timeout_seconds: float = _WORKER_TIMEOUT_SECONDS) -> None:
        self._timeout_seconds = max(float(timeout_seconds), 5.0)
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._fatal_error: str | None = None

    @property
    def available(self) -> bool:
        """Можно ли попытаться запустить локальное распознавание.

        Проверка намеренно не импортирует dlib. Сам импорт происходит только
        в дочернем процессе, чтобы несовместимый native wheel не мог уронить HA.
        """

        if self._fatal_error is not None:
            return False
        return all(
            importlib.util.find_spec(module_name) is not None
            for module_name in ("dlib", "face_recognition_models", "numpy", "PIL")
        )

    @property
    def fatal_error(self) -> str | None:
        """Вернуть причину окончательного отключения worker в текущем запуске."""

        return self._fatal_error

    def extract_single_encoding(self, image_bytes: bytes) -> list[float]:
        """Получить 128-мерный descriptor ровно одного лица."""

        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")

        response = self._rpc(
            {
                "command": "extract_single_encoding",
                "image": base64.b64encode(image_bytes).decode("ascii"),
            }
        )
        encoding = response.get("encoding")
        if not isinstance(encoding, list) or len(encoding) != 128:
            raise HomeAssistantError(
                "Движок распознавания вернул некорректный descriptor лица"
            )
        try:
            return [float(value) for value in encoding]
        except (TypeError, ValueError) as err:
            raise HomeAssistantError(
                "Движок распознавания вернул повреждённый descriptor лица"
            ) from err

    def recognize(
        self,
        image_bytes: bytes,
        known_faces: Sequence[tuple[str, Sequence[float]]],
        threshold: float,
    ) -> FaceRecognitionResult:
        """Распознать лица на кадре через изолированный worker."""

        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")

        response = self._rpc(
            {
                "command": "recognize",
                "image": base64.b64encode(image_bytes).decode("ascii"),
                "known_faces": [
                    {"name": str(name), "encoding": [float(value) for value in encoding]}
                    for name, encoding in known_faces
                ],
                "threshold": float(threshold),
            }
        )

        try:
            faces_detected = int(response.get("faces_detected", 0))
        except (TypeError, ValueError):
            faces_detected = 0

        matched_name = response.get("matched_name")
        if not isinstance(matched_name, str) or not matched_name:
            matched_name = None

        distance_raw = response.get("distance")
        try:
            distance = float(distance_raw) if distance_raw is not None else None
        except (TypeError, ValueError):
            distance = None

        return FaceRecognitionResult(
            faces_detected=max(faces_detected, 0),
            matched_name=matched_name,
            distance=distance,
        )

    def close(self) -> None:
        """Остановить worker при выгрузке config entry."""

        with self._lock:
            self._stop_worker_locked(reason="integration unload", mark_fatal=False)

    def _rpc(self, payload: dict[str, object]) -> dict[str, object]:
        """Выполнить один RPC-вызов к worker с подробной диагностикой."""

        with self._lock:
            if self._fatal_error is not None:
                raise HomeAssistantError(self._fatal_error)
            if not self.available:
                raise HomeAssistantError(
                    "Зависимости локального распознавания лиц не установлены"
                )

            process = self._ensure_worker_locked()
            self._request_id += 1
            request_id = self._request_id
            payload = dict(payload)
            payload["request_id"] = request_id
            command = str(payload.get("command") or "unknown")

            _LOGGER.debug(
                "Recognition worker request id=%s command=%s",
                request_id,
                command,
            )

            try:
                assert process.stdin is not None
                process.stdin.write(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                process.stdin.flush()
            except (BrokenPipeError, OSError) as err:
                message = self._worker_exit_message_locked(process, err)
                raise HomeAssistantError(message) from err

            assert process.stdout is not None
            try:
                ready, _, _ = select.select(
                    [process.stdout],
                    [],
                    [],
                    self._timeout_seconds,
                )
            except (OSError, ValueError) as err:
                self._stop_worker_locked(reason="select failure", mark_fatal=False)
                raise HomeAssistantError(
                    f"Не удалось дождаться ответа recognition worker: {err}"
                ) from err

            if not ready:
                _LOGGER.error(
                    "Recognition worker timeout id=%s command=%s timeout=%.1fs",
                    request_id,
                    command,
                    self._timeout_seconds,
                )
                self._stop_worker_locked(reason="timeout", mark_fatal=False)
                raise HomeAssistantError(
                    "Локальное распознавание не ответило вовремя; worker перезапущен"
                )

            line = process.stdout.readline()
            if not line:
                raise HomeAssistantError(self._worker_exit_message_locked(process))

            try:
                response = json.loads(line)
            except json.JSONDecodeError as err:
                _LOGGER.error(
                    "Recognition worker protocol error id=%s bytes=%s",
                    request_id,
                    len(line),
                )
                self._stop_worker_locked(reason="invalid JSON", mark_fatal=False)
                raise HomeAssistantError(
                    "Локальный движок распознавания вернул некорректный ответ"
                ) from err

            if not isinstance(response, dict):
                raise HomeAssistantError(
                    "Локальный движок распознавания вернул неожиданный формат"
                )

            response_id = response.get("request_id")
            if response_id != request_id:
                _LOGGER.error(
                    "Recognition worker response id mismatch expected=%s actual=%s",
                    request_id,
                    response_id,
                )
                self._stop_worker_locked(reason="request id mismatch", mark_fatal=False)
                raise HomeAssistantError(
                    "Нарушена синхронизация с локальным движком распознавания"
                )

            if not response.get("ok"):
                error_text = str(response.get("error") or "Неизвестная ошибка")
                error_code = str(response.get("error_code") or "worker_error")
                _LOGGER.warning(
                    "Recognition worker rejected id=%s command=%s code=%s error=%s",
                    request_id,
                    command,
                    error_code,
                    error_text,
                )
                raise HomeAssistantError(error_text)

            _LOGGER.debug(
                "Recognition worker response id=%s command=%s ok",
                request_id,
                command,
            )
            return response

    def _ensure_worker_locked(self) -> subprocess.Popen[str]:
        """Запустить worker, не импортируя native-библиотеки в Home Assistant."""

        process = self._process
        if process is not None and process.poll() is None:
            return process

        if process is not None:
            self._process = None

        worker_path = Path(__file__).with_name("worker.py")
        if not worker_path.is_file():
            raise HomeAssistantError(
                "Файл recognition worker отсутствует в установленной интеграции"
            )

        _LOGGER.info(
            "Запускаем изолированный recognition worker: python=%s",
            sys.executable,
        )
        try:
            process = subprocess.Popen(
                [sys.executable, "-u", str(worker_path)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                close_fds=True,
            )
        except OSError as err:
            raise HomeAssistantError(
                f"Не удалось запустить локальный recognition worker: {err}"
            ) from err

        self._process = process
        return process

    def _worker_exit_message_locked(
        self,
        process: subprocess.Popen[str],
        original_error: Exception | None = None,
    ) -> str:
        """Преобразовать аварийное завершение worker в безопасную ошибку HA."""

        try:
            return_code = process.poll()
            if return_code is None:
                return_code = process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            return_code = process.poll()

        if return_code is not None and return_code < 0:
            signal_number = -return_code
            try:
                signal_name = signal.Signals(signal_number).name
            except (ValueError, AttributeError):
                signal_name = f"signal {signal_number}"

            if signal_number in _FATAL_SIGNALS:
                message = (
                    "Локальный движок распознавания несовместим с этим CPU/окружением "
                    f"({signal_name}). Распознавание отключено до перезапуска Home Assistant; "
                    "остальные функции Интерсвязи продолжают работать."
                )
                self._fatal_error = message
                _LOGGER.error(
                    "Recognition worker аварийно завершён native-сигналом: %s. "
                    "Движок отключён, Home Assistant защищён от падения.",
                    signal_name,
                )
                self._process = None
                return message

        if return_code not in (None, 0):
            message = (
                "Локальный recognition worker аварийно завершился "
                f"(exit_code={return_code}). Распознавание отключено до перезапуска."
            )
            self._fatal_error = message
            _LOGGER.error(
                "Recognition worker exited unexpectedly: exit_code=%s original_error=%s",
                return_code,
                original_error,
            )
            self._process = None
            return message

        self._process = None
        return (
            "Локальный recognition worker завершил работу без ответа"
            + (f": {original_error}" if original_error else "")
        )

    def _stop_worker_locked(self, *, reason: str, mark_fatal: bool) -> None:
        """Корректно остановить дочерний процесс."""

        process = self._process
        self._process = None
        if process is None:
            return

        _LOGGER.debug(
            "Останавливаем recognition worker pid=%s reason=%s",
            process.pid,
            reason,
        )

        if process.poll() is None:
            try:
                if process.stdin is not None:
                    process.stdin.write(
                        json.dumps(
                            {"command": "shutdown", "request_id": -1},
                            separators=(",", ":"),
                        )
                        + "\n"
                    )
                    process.stdin.flush()
                process.wait(timeout=1.0)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                try:
                    process.terminate()
                    process.wait(timeout=1.0)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        process.kill()
                    except OSError:
                        pass

        if mark_fatal and self._fatal_error is None:
            self._fatal_error = f"Recognition worker остановлен: {reason}"
