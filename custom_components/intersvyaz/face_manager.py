"""Помощник для распознавания лиц и автооткрытия домофона."""
from __future__ import annotations

import asyncio
import io
import inspect
import logging
import time
import warnings
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable, List, Optional, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_FACE_ENCODING,
    CONF_FACE_NAME,
    CONF_KNOWN_FACES,
    DATA_FACE_MANAGER,
    DOMAIN,
    FACE_RECOGNITION_COOLDOWN_SECONDS,
    FACE_RECOGNITION_DISTANCE_THRESHOLD,
)

# Флаг для юнит-тестов и диагностики, сигнализирует о подавлении устаревшего предупреждения
# от зависимостей face_recognition. Важен для отслеживания корректной фильтрации предупреждений
# об устаревшем API pkg_resources.
_SUPPRESSED_PKG_RESOURCES_WARNING = False

try:
    # Библиотека face_recognition тянет за собой пакет face_recognition_models, который при импорте
    # генерирует предупреждение о грядущем удалении pkg_resources. Это предупреждение мешает
    # пользователям и засоряет журнал Home Assistant, поэтому при импорте временно подавляем его
    # через локальный фильтр, оставляя остальные уведомления без изменений.
    with warnings.catch_warnings(record=True) as suppressed_warnings:
        warnings.filterwarnings(
            "ignore",
            message="pkg_resources is deprecated as an API.",
            category=UserWarning,
            module="face_recognition_models",
        )
        import face_recognition  # type: ignore

    _SUPPRESSED_PKG_RESOURCES_WARNING = any(
        "pkg_resources is deprecated as an API." in str(item.message)
        for item in suppressed_warnings
    )
except ImportError:  # pragma: no cover - обработка отсутствия библиотеки
    face_recognition = None

_LOGGER = logging.getLogger(f"{DOMAIN}.face_manager")

# Если при импорте зависимости было подавлено предупреждение об устаревшем pkg_resources,
# зафиксируем это в логе для дальнейшей диагностики и контроля будущих обновлений.
if _SUPPRESSED_PKG_RESOURCES_WARNING:
    _LOGGER.debug(
        "Предупреждение об устаревшем pkg_resources от face_recognition_models было подавлено"
    )


@dataclass
class KnownFace:
    """Данные известного лица, сохранённые в настройках интеграции."""

    name: str
    encoding: List[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[float] | str]:
        """Преобразовать структуру в словарь для сериализации."""

        return {CONF_FACE_NAME: self.name, CONF_FACE_ENCODING: list(self.encoding)}


class FaceRecognitionManager:
    """Класс, отвечающий за подготовку и распознавание лиц на снимках."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        *,
        match_threshold: float = FACE_RECOGNITION_DISTANCE_THRESHOLD,
        cooldown_seconds: float = FACE_RECOGNITION_COOLDOWN_SECONDS,
    ) -> None:
        # Home Assistant и запись конфигурации, необходимые для обновления опций.
        self._hass = hass
        self._entry = entry
        # Порог схожести, ниже которого лицо считается совпадающим.
        self._match_threshold = float(match_threshold)
        # Интервал, защищающий от повторного открытия двери для одного домофона.
        self._cooldown_seconds = float(cooldown_seconds)
        # Список известных лиц, подготавливается при инициализации из опций.
        self._known_faces: list[KnownFace] = []
        # Запоминаем момент последнего автоматического открытия по каждому домофону.
        self._door_cooldown: dict[str, float] = {}
        # Блокировка защищает операции обновления и одновременной записи опций.
        self._lock = asyncio.Lock()
        # Флаг доступности библиотеки face_recognition.
        self._library_available = face_recognition is not None
        self._load_known_faces_from_entry(entry.options.get(CONF_KNOWN_FACES, []))

    @property
    def library_available(self) -> bool:
        """Сообщить, доступна ли зависимость face_recognition."""

        return self._library_available

    def _load_known_faces_from_entry(self, stored: Iterable[dict[str, object]]) -> None:
        """Загрузить список известных лиц из сохранённых опций записи."""

        self._known_faces.clear()
        for item in stored or []:
            if not isinstance(item, dict):
                continue
            name = item.get(CONF_FACE_NAME)
            encoding = item.get(CONF_FACE_ENCODING)
            if not isinstance(name, str):
                continue
            if not isinstance(encoding, Iterable):
                continue
            try:
                vector = [float(value) for value in encoding]
            except (TypeError, ValueError):
                _LOGGER.debug("Игнорируем повреждённые данные лица %s", item)
                continue
            self._known_faces.append(KnownFace(name=name, encoding=vector))
        if self._known_faces:
            _LOGGER.info(
                "Загружено %s известных лиц для автоматического открытия", len(self._known_faces)
            )
        else:
            _LOGGER.info("Известные лица для автоматического открытия не заданы")

    def list_known_faces(self) -> list[KnownFace]:
        """Вернуть копию текущего списка известных лиц для отображения в UI."""

        return list(self._known_faces)

    def list_known_face_names(self) -> list[str]:
        """Вернуть список имён известных лиц без раскрытия векторов признаков."""

        return [face.name for face in self._known_faces]

    async def async_add_known_face(self, name: str, image_bytes: bytes) -> None:
        """Добавить новое известное лицо, вычислив вектор признаков."""

        if not self._library_available:
            raise HomeAssistantError(
                "Библиотека face_recognition не установлена, автоматическое распознавание недоступно"
            )
        if not image_bytes:
            raise HomeAssistantError("Пустое изображение невозможно обработать")

        async with self._lock:
            encoding = await self._hass.async_add_executor_job(
                self._extract_encoding, image_bytes
            )
            # Удаляем ранее сохранённые записи с тем же именем, чтобы не плодить дубликаты.
            self._known_faces = [face for face in self._known_faces if face.name != name]
            self._known_faces.append(KnownFace(name=name, encoding=encoding))
            await self._async_store_faces()
            _LOGGER.info(
                "Добавлено новое известное лицо '%s' (%s признаков)", name, len(encoding)
            )

    async def async_remove_known_face(self, name: str) -> None:
        """Удалить лицо из справочника по его имени."""

        async with self._lock:
            before = len(self._known_faces)
            self._known_faces = [face for face in self._known_faces if face.name != name]
            if len(self._known_faces) == before:
                raise HomeAssistantError(
                    f"Лицо с именем '{name}' не найдено в интеграции Intersvyaz"
                )
            await self._async_store_faces()
            _LOGGER.info("Удалено известное лицо '%s'", name)

    async def async_process_image(
        self,
        door_uid: str,
        image_bytes: bytes,
        open_callback: Callable[[], Optional[Awaitable[None]]],
    ) -> None:
        """Проанализировать изображение домофона и открыть дверь при совпадении."""

        if not self._library_available:
            _LOGGER.debug(
                "Распознавание лиц отключено для домофона uid=%s: библиотека недоступна",
                door_uid,
            )
            return
        if not self._known_faces:
            _LOGGER.debug(
                "Распознавание лиц пропущено для uid=%s: список известных лиц пуст", door_uid
            )
            return
        if not image_bytes:
            _LOGGER.debug(
                "Получено пустое изображение для uid=%s, распознавание пропущено", door_uid
            )
            return
        if not callable(open_callback):
            _LOGGER.warning(
                "Невозможно открыть домофон uid=%s автоматически: отсутствует колбэк", door_uid
            )
            return

        now = time.monotonic()
        last_open = self._door_cooldown.get(door_uid, 0)
        if now - last_open < self._cooldown_seconds:
            _LOGGER.debug(
                "Домофон uid=%s недавно открывался автоматически (%.1f с назад), пропускаем",
                door_uid,
                now - last_open,
            )
            return

        try:
            match_name = await self._hass.async_add_executor_job(
                self._match_known_faces, image_bytes
            )
        except HomeAssistantError as err:
            _LOGGER.error(
                "Ошибка анализа лиц для домофона uid=%s: %s", door_uid, err
            )
            return

        if not match_name:
            _LOGGER.debug("На снимке домофона uid=%s не найдено знакомых лиц", door_uid)
            return

        _LOGGER.info(
            "Распознано знакомое лицо '%s' для домофона uid=%s, инициируем открытие",
            match_name,
            door_uid,
        )
        try:
            result = open_callback()
            if asyncio.iscoroutine(result):
                await result
        except Exception as err:  # pragma: no cover - защитный сценарий
            _LOGGER.error(
                "Не удалось автоматически открыть домофон uid=%s по лицу '%s': %s",
                door_uid,
                match_name,
                err,
            )
            return

        self._door_cooldown[door_uid] = time.monotonic()

    def _extract_encoding(self, image_bytes: bytes) -> List[float]:
        """Вычислить вектор признаков лица на изображении (в блокирующем потоке)."""

        if face_recognition is None:
            raise HomeAssistantError(
                "Библиотека face_recognition не установлена, распознавание недоступно"
            )
        try:
            image_stream = io.BytesIO(image_bytes)
            image = face_recognition.load_image_file(image_stream)
        except Exception as err:  # type: ignore
            raise HomeAssistantError(f"Не удалось загрузить изображение: {err}") from err

        encodings = face_recognition.face_encodings(image)
        if not encodings:
            raise HomeAssistantError("На изображении не найдено лиц")

        encoding = encodings[0]
        return [float(value) for value in list(encoding)]

    def _match_known_faces(self, image_bytes: bytes) -> Optional[str]:
        """Найти имя знакомого лица на изображении или вернуть None."""

        if face_recognition is None:
            raise HomeAssistantError("Библиотека face_recognition недоступна")
        try:
            image_stream = io.BytesIO(image_bytes)
            image = face_recognition.load_image_file(image_stream)
        except Exception as err:  # type: ignore
            raise HomeAssistantError(f"Не удалось загрузить изображение: {err}") from err

        encodings = face_recognition.face_encodings(image)
        if not encodings:
            return None

        known_vectors = [face.encoding for face in self._known_faces]
        known_names = [face.name for face in self._known_faces]

        best_match: tuple[str, float] | None = None
        for encoding in encodings:
            try:
                distances = face_recognition.face_distance(known_vectors, encoding)
            except Exception as err:  # type: ignore
                raise HomeAssistantError(f"Ошибка сравнения лиц: {err}") from err

            distance_values = self._normalize_distances(distances)
            if not distance_values:
                continue
            best_distance = min(distance_values)
            if best_distance <= self._match_threshold:
                best_index = distance_values.index(best_distance)
                candidate = known_names[best_index]
                if not best_match or best_distance < best_match[1]:
                    best_match = (candidate, best_distance)

        if not best_match:
            return None

        _LOGGER.debug(
            "Лучшее совпадение лица '%s' с дистанцией %.3f", best_match[0], best_match[1]
        )
        return best_match[0]

    @staticmethod
    def _normalize_distances(distances: Iterable[float] | object) -> List[float]:
        """Преобразовать массив расстояний в обычный список чисел."""

        if distances is None:
            return []
        if isinstance(distances, list):
            return [float(value) for value in distances]
        if isinstance(distances, tuple):
            return [float(value) for value in list(distances)]
        if hasattr(distances, "tolist"):
            try:
                return [float(value) for value in list(distances.tolist())]
            except Exception:  # pragma: no cover - защитная ветка
                return []
        try:
            return [float(distances)]
        except Exception:  # pragma: no cover - защитная ветка
            return []

    async def _async_store_faces(self) -> None:
        """Сохранить актуальный список лиц в опциях записи конфигурации."""

        # Создаём копию опций, чтобы не модифицировать исходный словарь записи напрямую.
        options = dict(self._entry.options)
        options[CONF_KNOWN_FACES] = [face.as_dict() for face in self._known_faces]

        # Метод async_update_entry в Home Assistant синхронный, однако сторонние тесты
        # или будущие версии могут вернуть awaitable. Чтобы интеграция была устойчива,
        # проверяем результат и ожидаем его только при необходимости.
        update_result = self._hass.config_entries.async_update_entry(
            self._entry, options=options
        )

        # В продуктивной среде метод возвращает None/True, однако сторонние плагины или
        # будущие версии Home Assistant могут вернуть awaitable. Более того, существует
        # реальный кейс, когда вспомогательные обёртки помечают булево значение как
        # awaitable (inspect.isawaitable -> True), что приводит к попытке ожидания bool
        # и аварийному завершению сервиса. Поэтому явно обрабатываем булевые значения и
        # любые другие синхронные результаты прежде, чем проверять признак awaitable.
        if isinstance(update_result, bool):
            _LOGGER.debug(
                "Синхронное обновление опций вернуло булев результат %s", update_result
            )
        elif inspect.isawaitable(update_result):
            try:
                await cast(Awaitable[object], update_result)
                _LOGGER.debug("Асинхронное обновление опций успешно завершилось")
            except TypeError as err:
                _LOGGER.warning(
                    "Метод async_update_entry вернул объект, который нельзя ожидать: %s",
                    err,
                )
        elif update_result is not None:
            _LOGGER.debug(
                "Метод async_update_entry вернул неожиданный синхронный результат %r",
                update_result,
            )

        _LOGGER.debug(
            "Сохранён список из %s известных лиц для записи %s",
            len(self._known_faces),
            self._entry.entry_id,
        )

        # Обновляем кеш менеджера в hass.data, чтобы другие части интеграции
        # могли мгновенно получить доступ к свежему экземпляру менеджера.
        domain_store = self._hass.data.setdefault(DOMAIN, {})
        entry_store = domain_store.setdefault(self._entry.entry_id, {})
        entry_store[DATA_FACE_MANAGER] = self

