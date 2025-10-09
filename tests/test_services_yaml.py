"""Тесты корректности файла services.yaml для интеграции Intersvyaz."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest


@pytest.fixture
def services_file() -> Path:
    """Вернуть путь к services.yaml внутри репозитория."""

    repo_root = Path(__file__).resolve().parent.parent
    services_path = repo_root / "custom_components" / "intersvyaz" / "services.yaml"
    assert services_path.exists(), "services.yaml должен существовать для описания сервисов"
    return services_path


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Простейший парсер YAML, поддерживающий вложенные словари по отступам.

    Home Assistant загружает services.yaml обычным YAML-парсером PyYAML, однако
    тестовая среда репозитория не содержит зависимости PyYAML. Чтобы гарантировать
    корректность структуры services.yaml, реализуем небольшой парсер, который
    понимает только словари, используемые в файле описания сервисов. Если формат
    изменится (например, появятся списки), тест сразу подскажет о необходимости
    обновить парсер.
    """

    stack: List[Dict[str, Any]] = [{}]
    indent_stack: List[int] = [-1]

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        # Пропускаем пустые строки и комментарии, они не влияют на структуру файла.
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        while indent <= indent_stack[-1] and len(stack) > 1:
            stack.pop()
            indent_stack.pop()

        if ":" not in stripped:
            raise AssertionError(
                f"Строка '{raw_line}' должна содержать ключ и двоеточие"
            )

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"')

        current = stack[-1]
        if value:
            # Сохраняем значение как строку без кавычек.
            current[key] = value
        else:
            # Начинаем новый вложенный словарь и поднимаем уровень стека.
            nested: Dict[str, Any] = {}
            current[key] = nested
            stack.append(nested)
            indent_stack.append(indent)

    return stack[0]


def test_services_yaml_structure(services_file: Path) -> None:
    """Проверяем, что services.yaml содержит описание всех зарегистрированных сервисов."""

    parsed = _parse_simple_yaml(services_file.read_text(encoding="utf-8"))
    expected_services = {
        "open_door": {"required_fields": {"entry_id"}, "optional_fields": {"door_uid"}},
        "add_known_face": {
            "required_fields": {"entry_id", "name"},
            "optional_fields": {"image_url", "image_base64"},
        },
        "remove_known_face": {
            "required_fields": {"entry_id", "name"},
            "optional_fields": set(),
        },
    }

    assert parsed.keys() >= expected_services.keys(), (
        "services.yaml обязан описывать все публичные сервисы интеграции"
    )

    for service, field_info in expected_services.items():
        service_block = parsed.get(service)
        assert isinstance(service_block, dict), f"Раздел {service} должен быть словарём"

        # Проверяем наличие названия и описания для интерфейса Home Assistant.
        for key in ("name", "description"):
            assert service_block.get(key), f"Сервис {service} обязан иметь {key}"

        fields = service_block.get("fields")
        assert isinstance(fields, dict) and fields, (
            f"Сервис {service} обязан объявлять поля во вложенном разделе fields"
        )

        declared_fields = set(fields.keys())
        assert field_info["required_fields"].issubset(declared_fields), (
            f"Сервис {service} обязан описывать все обязательные поля"
        )
        assert declared_fields.issuperset(field_info["optional_fields"]), (
            f"Сервис {service} обязан перечислять опциональные поля"
        )

        # Каждое поле должно содержать понятные подсказки для интерфейса.
        for field_name, field_block in fields.items():
            assert isinstance(field_block, dict), (
                f"Поле {field_name} сервиса {service} должно быть словарём свойств"
            )
            for key in ("name", "description"):
                assert field_block.get(key), (
                    f"Поле {field_name} сервиса {service} должно содержать {key}"
                )

            # Дополнительно убеждаемся, что флаг required указан явно, чтобы пользователю было видно требования.
            assert "required" in field_block, (
                f"Поле {field_name} сервиса {service} должно указывать required"
            )

            # Пример помогает формировать подсказку в интерфейсе и важен для документации.
            assert field_block.get("example"), (
                f"Поле {field_name} сервиса {service} должно приводить пример использования"
            )
