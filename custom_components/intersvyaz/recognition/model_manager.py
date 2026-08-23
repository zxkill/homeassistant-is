"""Automatic installer for the public-domain dlib face models."""
from __future__ import annotations

import asyncio
import bz2
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger("custom_components.intersvyaz.recognition.models")

_MODEL_DOWNLOAD_TIMEOUT_SECONDS = 180
_MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
_MAX_MODEL_BYTES = 64 * 1024 * 1024
_MODEL_LOCK = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class _ModelSpec:
    filename: str
    url: str
    archive_sha256: str


_MODELS = (
    _ModelSpec(
        filename="shape_predictor_5_face_landmarks.dat",
        url="https://github.com/davisking/dlib-models/raw/master/shape_predictor_5_face_landmarks.dat.bz2",
        archive_sha256="6e787bbebf5c9efdb793f6cd1f023230c4413306605f24f299f12869f95aa472",
    ),
    _ModelSpec(
        filename="dlib_face_recognition_resnet_model_v1.dat",
        url="https://github.com/davisking/dlib-models/raw/master/dlib_face_recognition_resnet_model_v1.dat.bz2",
        archive_sha256="abb1f61041e434465855ce81c2bd546e830d28bcbed8d27ffbe5bb408b11553a",
    ),
)


class FaceModelManager:
    """Download and verify only the two dlib models used by the integration."""

    def __init__(self, hass: HomeAssistant, model_dir: Path) -> None:
        self._hass = hass
        self._model_dir = model_dir
        self._ready = False

    @property
    def model_dir(self) -> Path:
        return self._model_dir

    async def async_ensure_models(self) -> Path:
        """Ensure both models are present and cryptographically verified."""

        if self._ready:
            return self._model_dir

        async with _MODEL_LOCK:
            if self._ready:
                return self._model_dir

            await self._hass.async_add_executor_job(
                self._model_dir.mkdir, 0o755, True, True
            )

            for spec in _MODELS:
                valid = await self._hass.async_add_executor_job(
                    _validate_installed_model,
                    self._model_dir,
                    spec,
                )
                if valid:
                    _LOGGER.debug(
                        "[FACE][MODEL_READY] file=%s source=cache",
                        spec.filename,
                    )
                    continue
                await self._async_download_model(spec)

            self._ready = True
            _LOGGER.info(
                "[FACE][MODELS_READY] count=%s directory=%s",
                len(_MODELS),
                self._model_dir.name,
            )
            return self._model_dir

    async def _async_download_model(self, spec: _ModelSpec) -> None:
        """Download one official compressed model and install it atomically."""

        _LOGGER.info(
            "[FACE][MODEL_DOWNLOAD_BEGIN] file=%s host=github.com/davisking/dlib-models",
            spec.filename,
        )
        session = async_get_clientsession(self._hass)
        timeout = ClientTimeout(total=_MODEL_DOWNLOAD_TIMEOUT_SECONDS)
        try:
            async with session.get(spec.url, timeout=timeout) as response:
                if response.status != 200:
                    raise HomeAssistantError(
                        f"Не удалось скачать модель {spec.filename}: HTTP {response.status}"
                    )
                content_length = response.content_length
                if content_length is not None and content_length > _MAX_ARCHIVE_BYTES:
                    raise HomeAssistantError(
                        f"Архив модели {spec.filename} имеет неожиданный размер"
                    )
                archive = await response.read()
        except (ClientError, TimeoutError) as err:
            _LOGGER.warning(
                "[FACE][MODEL_DOWNLOAD_FAILED] file=%s error=%s",
                spec.filename,
                err.__class__.__name__,
            )
            raise HomeAssistantError(
                "Не удалось автоматически скачать модели распознавания лиц. "
                "Проверьте доступ Home Assistant в интернет и повторите попытку."
            ) from err

        if not archive or len(archive) > _MAX_ARCHIVE_BYTES:
            raise HomeAssistantError(
                f"Архив модели {spec.filename} пустой или имеет неожиданный размер"
            )

        archive_hash = hashlib.sha256(archive).hexdigest()
        if archive_hash != spec.archive_sha256:
            _LOGGER.error(
                "[FACE][MODEL_HASH_MISMATCH] file=%s bytes=%s",
                spec.filename,
                len(archive),
            )
            raise HomeAssistantError(
                f"Контрольная сумма модели {spec.filename} не совпала. "
                "Файл не будет использоваться."
            )

        model_size, model_hash = await self._hass.async_add_executor_job(
            _install_verified_archive,
            self._model_dir,
            spec,
            archive,
        )
        _LOGGER.info(
            "[FACE][MODEL_DOWNLOAD_OK] file=%s archive_bytes=%s model_bytes=%s "
            "model_sha256_prefix=%s",
            spec.filename,
            len(archive),
            model_size,
            model_hash[:12],
        )


def _validate_installed_model(model_dir: Path, spec: _ModelSpec) -> bool:
    model_path = model_dir / spec.filename
    marker_path = model_dir / f"{spec.filename}.verified.json"
    if not model_path.is_file() or not marker_path.is_file():
        return False

    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        expected_size = int(marker.get("size", 0))
        expected_model_hash = str(marker.get("model_sha256", ""))
        expected_source_hash = str(marker.get("archive_sha256", ""))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False

    if (
        expected_source_hash != spec.archive_sha256
        or expected_size <= 0
        or not expected_model_hash
        or model_path.stat().st_size != expected_size
    ):
        return False

    actual_hash = _sha256_file(model_path)
    return actual_hash == expected_model_hash


def _install_verified_archive(
    model_dir: Path,
    spec: _ModelSpec,
    archive: bytes,
) -> tuple[int, str]:
    try:
        model_bytes = bz2.decompress(archive)
    except OSError as err:
        raise HomeAssistantError(
            f"Не удалось распаковать модель {spec.filename}"
        ) from err

    if not model_bytes or len(model_bytes) > _MAX_MODEL_BYTES:
        raise HomeAssistantError(
            f"Распакованная модель {spec.filename} имеет неожиданный размер"
        )

    model_hash = hashlib.sha256(model_bytes).hexdigest()
    model_path = model_dir / spec.filename
    temp_path = model_dir / f".{spec.filename}.tmp"
    marker_path = model_dir / f"{spec.filename}.verified.json"
    marker_temp = model_dir / f".{spec.filename}.verified.tmp"

    temp_path.write_bytes(model_bytes)
    temp_path.replace(model_path)

    marker = {
        "archive_sha256": spec.archive_sha256,
        "model_sha256": model_hash,
        "size": len(model_bytes),
        "source": "github.com/davisking/dlib-models",
    }
    marker_temp.write_text(
        json.dumps(marker, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    marker_temp.replace(marker_path)
    return len(model_bytes), model_hash


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
