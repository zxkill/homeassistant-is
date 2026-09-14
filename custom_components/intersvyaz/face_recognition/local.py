class LocalFaceRecognitionBackend(FaceRecognitionBackend):
    async def recognize(self, image: bytes) -> Optional[str]:
        return await hass.async_add_executor_job(
            self._match_known_faces, image
        )

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
