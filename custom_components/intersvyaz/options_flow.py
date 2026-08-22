"""UI-настройки распознавания лиц Intersvyaz."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigEntry, OptionsFlow, ConfigFlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    SNAPSHOT_MAX_BYTES,
    CONF_AUTO_OPEN_COOLDOWN_SECONDS,
    CONF_BACKGROUND_CAMERAS,
    CONF_FACE_EVENT_COOLDOWN_SECONDS,
    CONF_FACE_IMAGE,
    CONF_FACE_NAME,
    CONF_RECOGNITION_MODE,
    CONF_RECOGNITION_REQUIRED_MATCHES,
    CONF_RECOGNITION_THRESHOLD,
    DEFAULT_RECOGNITION_MODE,
    FACE_EVENT_COOLDOWN_SECONDS,
    FACE_RECOGNITION_COOLDOWN_SECONDS,
    FACE_RECOGNITION_DISTANCE_THRESHOLD,
    FACE_REQUIRED_MATCHES_DEFAULT,
    FACE_REQUIRED_MATCHES_MAX,
    FACE_REQUIRED_MATCHES_MIN,
    RECOGNITION_MODE_AUTO_OPEN,
    RECOGNITION_MODE_OBSERVE,
    RECOGNITION_MODE_OFF,
)
from .runtime import IntersvyazConfigEntry

_LOGGER = logging.getLogger("custom_components.intersvyaz.options_flow")


class IntersvyazOptionsFlow(OptionsFlow):
    """Настройки лиц, камер и безопасного автооткрытия."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._last_error: str | None = None

    @property
    def _typed_entry(self) -> IntersvyazConfigEntry:
        return self._entry  # type: ignore[return-value]

    def _recognition_mode_label(self, mode: str) -> str:
        """Вернуть человекочитаемое название режима для сводки настроек."""

        language = (self.hass.config.language or "en").split("-")[0].lower()
        labels = {
            "ru": {
                RECOGNITION_MODE_OFF: "Выключено",
                RECOGNITION_MODE_OBSERVE: "Только распознавать",
                RECOGNITION_MODE_AUTO_OPEN: "Распознавать и автоматически открывать",
            },
            "en": {
                RECOGNITION_MODE_OFF: "Off",
                RECOGNITION_MODE_OBSERVE: "Recognize only",
                RECOGNITION_MODE_AUTO_OPEN: "Recognize and open automatically",
            },
        }
        label = labels.get(language, labels["en"]).get(mode, mode)
        _LOGGER.debug(
            "[OPTIONS_FLOW][MODE_LABEL] entry_id=%s mode=%s language=%s label=%s",
            self._entry.entry_id,
            mode,
            language,
            label,
        )
        return label

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Главное меню настроек."""

        manager = self._typed_entry.runtime_data.face_manager
        names = manager.list_known_face_names()
        menu_options = ["recognition_settings", "add_face"]
        if names:
            menu_options.append("remove_face")
        if any(door.has_video and door.image_url for door in self._typed_entry.runtime_data.doors):
            menu_options.append("background_cameras")

        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
            description_placeholders={
                "known_faces": self._format_names(names),
                "recognition_mode": self._recognition_mode_label(manager.recognition_mode),
                "error_message": self._last_error or "",
            },
        )

    async def async_step_recognition_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Настроить режим, порог и подтверждения автооткрытия."""

        options = self._entry.options
        language = (self.hass.config.language or "en").split("-")[0].lower()
        if language == "ru":
            mode_labels = {
                RECOGNITION_MODE_OFF: "Выключено",
                RECOGNITION_MODE_OBSERVE: "Только распознавать",
                RECOGNITION_MODE_AUTO_OPEN: "Распознавать и автоматически открывать",
            }
        else:
            mode_labels = {
                RECOGNITION_MODE_OFF: "Off",
                RECOGNITION_MODE_OBSERVE: "Recognize only",
                RECOGNITION_MODE_AUTO_OPEN: "Recognize and open automatically",
            }
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RECOGNITION_MODE,
                    default=options.get(CONF_RECOGNITION_MODE, DEFAULT_RECOGNITION_MODE),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=value, label=label)
                            for value, label in mode_labels.items()
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_RECOGNITION_THRESHOLD,
                    default=_bounded_float(
                        options.get(
                            CONF_RECOGNITION_THRESHOLD,
                            FACE_RECOGNITION_DISTANCE_THRESHOLD,
                        ),
                        default=FACE_RECOGNITION_DISTANCE_THRESHOLD,
                        minimum=0.10,
                        maximum=0.55,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.10,
                        max=0.55,
                        step=0.01,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_RECOGNITION_REQUIRED_MATCHES,
                    default=int(
                        options.get(
                            CONF_RECOGNITION_REQUIRED_MATCHES,
                            FACE_REQUIRED_MATCHES_DEFAULT,
                        )
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=FACE_REQUIRED_MATCHES_MIN,
                        max=FACE_REQUIRED_MATCHES_MAX,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_AUTO_OPEN_COOLDOWN_SECONDS,
                    default=float(
                        options.get(
                            CONF_AUTO_OPEN_COOLDOWN_SECONDS,
                            FACE_RECOGNITION_COOLDOWN_SECONDS,
                        )
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5,
                        max=600,
                        step=5,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Required(
                    CONF_FACE_EVENT_COOLDOWN_SECONDS,
                    default=float(
                        options.get(
                            CONF_FACE_EVENT_COOLDOWN_SECONDS,
                            FACE_EVENT_COOLDOWN_SECONDS,
                        )
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=300,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
            }
        )

        if user_input is None:
            return self.async_show_form(
                step_id="recognition_settings",
                data_schema=schema,
            )

        new_options = dict(options)
        new_options.update(user_input)
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        self._typed_entry.runtime_data.face_manager.refresh_options()
        await self._typed_entry.runtime_data.background_processor.async_refresh_from_options()
        _LOGGER.info(
            "Recognition settings обновлены: entry_id=%s mode=%s",
            self._entry.entry_id,
            user_input.get(CONF_RECOGNITION_MODE),
        )
        return await self.async_step_init()

    async def async_step_add_face(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Добавить лицо через штатный FileSelector Home Assistant."""

        manager = self._typed_entry.runtime_data.face_manager
        errors: dict[str, str] = {}
        schema = vol.Schema(
            {
                vol.Required(CONF_FACE_NAME): selector.TextSelector(),
                vol.Required(CONF_FACE_IMAGE): selector.FileSelector(
                    selector.FileSelectorConfig(
                        accept=".jpg,.jpeg,.png,image/jpeg,image/png"
                    )
                ),
            }
        )

        if user_input is not None:
            name = str(user_input.get(CONF_FACE_NAME, "")).strip()
            upload_id = user_input.get(CONF_FACE_IMAGE)
            try:
                image_bytes = await self.hass.async_add_executor_job(
                    self._read_uploaded_file, str(upload_id)
                )
                await manager.async_add_known_face(name, image_bytes)
            except (HomeAssistantError, OSError, ValueError) as err:
                _LOGGER.warning("Не удалось добавить лицо: %s", err)
                self._last_error = str(err)
                errors["base"] = "add_failed"
            else:
                self._last_error = None
                await self._typed_entry.runtime_data.background_processor.async_refresh_from_options()
                return await self.async_step_init()

        return self.async_show_form(
            step_id="add_face",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "known_faces": self._format_names(manager.list_known_face_names()),
                "error_message": self._last_error or "",
            },
        )

    async def async_step_remove_face(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Удалить ранее добавленное лицо."""

        manager = self._typed_entry.runtime_data.face_manager
        names = manager.list_known_face_names()
        if not names:
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Required(CONF_FACE_NAME): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sorted(names),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get(CONF_FACE_NAME, ""))
            try:
                await manager.async_remove_known_face(name)
            except HomeAssistantError as err:
                self._last_error = str(err)
                errors["base"] = "remove_failed"
            else:
                self._last_error = None
                await self._typed_entry.runtime_data.background_processor.async_refresh_from_options()
                return await self.async_step_init()

        return self.async_show_form(
            step_id="remove_face",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "known_faces": self._format_names(names),
                "error_message": self._last_error or "",
            },
        )

    async def async_step_background_cameras(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Выбрать камеры для фонового анализа."""

        doors = [
            door
            for door in self._typed_entry.runtime_data.doors
            if door.has_video and door.image_url
        ]
        if not doors:
            self._last_error = "Нет доступных домофонов с камерой"
            return await self.async_step_init()

        choices = {door.uid: door.address or "Домофон" for door in doors}
        selected = self._entry.options.get(CONF_BACKGROUND_CAMERAS, [])
        if not isinstance(selected, list):
            selected = []

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BACKGROUND_CAMERAS,
                    default=[uid for uid in selected if uid in choices],
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=uid, label=label)
                            for uid, label in choices.items()
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

        if user_input is None:
            return self.async_show_form(
                step_id="background_cameras",
                data_schema=schema,
                description_placeholders={
                    "available_doors": "\n".join(f"• {label}" for label in choices.values())
                },
            )

        new_options = dict(self._entry.options)
        value = user_input.get(CONF_BACKGROUND_CAMERAS, [])
        new_options[CONF_BACKGROUND_CAMERAS] = [uid for uid in value if uid in choices]
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)
        await self._typed_entry.runtime_data.background_processor.async_refresh_from_options()
        _LOGGER.info(
            "Background cameras обновлены: entry_id=%s count=%s",
            self._entry.entry_id,
            len(new_options[CONF_BACKGROUND_CAMERAS]),
        )
        return await self.async_step_init()

    def _read_uploaded_file(self, upload_id: str) -> bytes:
        if not upload_id:
            raise HomeAssistantError("Файл изображения не выбран")
        with process_uploaded_file(self.hass, upload_id) as path:
            file_path = Path(path)
            if file_path.stat().st_size > SNAPSHOT_MAX_BYTES:
                raise HomeAssistantError("Изображение слишком большое")
            data = file_path.read_bytes()
        if not data:
            raise HomeAssistantError("Загруженный файл пуст")
        return data

    @staticmethod
    def _format_names(names: list[str]) -> str:
        if not names:
            return "Пока не добавлено ни одного лица."
        return "\n".join(f"• {name}" for name in sorted(names))


def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    """Normalize a persisted numeric option before feeding a NumberSelector."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(float(minimum), min(float(maximum), parsed))
