# Changelog

## 2.0.14

- Полностью переосмыслена работа live-video после прямой диагностики CDN: `MEDIA.HLS.LIVE.MAIN` сам по себе является нормальным rolling HLS — media playlist содержит 3 MPEG-TS сегмента, не имеет `#EXT-X-ENDLIST`, а `MEDIA-SEQUENCE` и последний segment URI регулярно меняются. Поведение одинаково для Python/Chrome/Safari User-Agent.
- Удалён из runtime-пути локальный HLS proxy 2.0.11–2.0.13 и искусственная адаптация односегментного playlist: они были основаны на неверной диагностической гипотезе и сами меняли поведение рабочего CDN-потока.
- `stream_source()` снова отдаёт оригинальный `MEDIA.HLS.LIVE.MAIN` напрямую штатному Home Assistant Stream/PyAV/go2rtc.
- `LOW_LATENCY` (`realtime=1`) больше не используется как fallback стандартного HA stream: именно этот URL присутствовал в ранних ошибках PyAV `Invalid data found when processing input`.
- Probe `MAIN` больше не добавляет нестандартные `Authorization`, `Origin` и `Referer`; запрос остаётся максимально близким к успешно проверенному прямому CDN-запросу.
- Добавлены безопасные логи `[YARD_STREAM][DIRECT_SOURCE]` и `[YARD_STREAM][LOW_LATENCY_ONLY]` без URL, bearer, UUID и адресов.
- Старые `yard_hls_proxy.py`, `yard_hls_utils.py` и `yard_live_playlist.py` больше не импортируются и могут быть удалены из репозитория после применения patch.

## 2.0.13

- Реальный HLS-тест показал vendor-specific поведение CDN Интерсвязи: media playlist для live-камеры содержит один MPEG-TS сегмент примерно на 10 секунд и `#EXT-X-ENDLIST`. Для Home Assistant/PyAV/go2rtc это корректный конечный VOD-клип, поэтому поток закономерно завершался через один сегмент.
- Добавлен отдельный `yard_live_playlist.py`: только такой односегментный `ENDLIST`-playlist преобразуется в небольшой rolling live playlist без `ENDLIST`.
- При каждом повторном запросе upstream media playlist новый segment URI получает собственный монотонный `#EXT-X-MEDIA-SEQUENCE`; proxy держит окно последних четырёх сегментов. Стандартные live HLS и обычные многосегментные VOD playlists не изменяются.
- Добавлены безопасные логи `[YARD_HLS_PROXY][LIVE_ADAPT]`/`[LIVE_WAIT]` с sequence/window без URL, токенов, UUID и адресов.
- Добавлены regression-тесты на первый segment, повторный poll, продвижение sequence, rolling window и отсутствие вмешательства в обычный HLS.
## 2.0.12

- Исправлен HLS proxy после реального теста Home Assistant/PyAV/go2rtc: вложенный playlist теперь определяется не только по `.m3u8`/Content-Type, но и по фактической сигнатуре `#EXTM3U`. Это закрывает CDN endpoints с неточным MIME или URL без расширения.
- Bearer query теперь сохраняется даже после upstream redirect: перед разбором дочерних URI токен переносится с исходного авторизованного URL на финальный URL ответа.
- Для upstream HLS запросов query bearer дополнительно дублируется в стандартный `Authorization: Bearer ...`, а также отправляются безопасные `Origin`/`Referer` камеры. Значения credentials никогда не логируются.
- Binary media proxy теперь сначала безопасно читает небольшой prefix для определения формата, затем отдаёт prefix и оставшийся поток без потери первых байтов.
- Добавлены подробные stage-логи `[YARD_HLS_PROXY][ROOT_REQUEST|PLAYLIST_OK|LOCAL_MISS|LOCAL_DENY|UPSTREAM_ERROR|NETWORK_ERROR]` без URL, UUID, адресов и токенов.
- HLS URL helpers вынесены в отдельный `yard_hls_utils.py`, чтобы URL/auth наследование тестировалось без загрузки Home Assistant.
## 2.0.11

- Живой HLS больше не передаёт bearer-CDN URL напрямую в Home Assistant/FFmpeg/go2rtc. Добавлен локальный runtime HLS proxy внутри Home Assistant.
- Proxy переписывает master/media playlists и все URI сегментов/ключей на локальные URL, поэтому bearer-параметр явно наследуется вложенными HLS ресурсами вместо потери при разрешении относительных ссылок.
- Upstream bearer, UUID и media URL больше не попадают в ошибки `stream`/`go2rtc`; локальный proxy использует отдельный runtime-secret в query `auth`, который не связан с учётными данными Интерсвязи.
- Обычный `MEDIA.HLS.LIVE.MAIN` теперь предпочитается как наиболее совместимый с FFmpeg/go2rtc; `LOW_LATENCY` используется как fallback.
- Proxy поддерживает вложенные `.m3u8`, `URI=...` в HLS tags, media segments и HTTP Range; ресурсы живут только в памяти и автоматически очищаются.
- Добавлены подробные `[YARD_HLS_PROXY]` логи без токенов, адресов, UUID и upstream URL.

## 2.0.10

- Исправлено ошибочное сопоставление yard-камеры только по номеру подъезда: камера подъезда одного дома больше не может привязаться к домофону другого адреса с тем же номером подъезда. Это исправляет пропавшие camera-only устройства (например, пятый подъезд).
- Живой поток теперь сначала проверяет `MEDIA.HLS.LIVE.LOW_LATENCY`, затем обычный `MAIN`; в Home Assistant передаётся только URL, реально отдающий HLS playlist.
- Если временный bearer в media URL устарел, перед запуском потока каталог `/api/yard-with-group` автоматически обновляется и HLS проверяется повторно.
- Добавлен отдельный безопасный `YardStreamResolver`: подробные `[YARD_STREAM]` логи не содержат URL, токены, UUID, MAC или адреса.
- Сброс HLS probe-cache выполняется вместе с обновлением каталога камер.

## 2.0.9

- Известные лица теперь можно связывать с существующими сущностями `person.*` Home Assistant вместо отдельного локального имени.
- В UI «Добавить лицо» используется штатный EntitySelector, отфильтрованный по домену `person`; отображаемое имя берётся из актуального `friendly_name` Home Assistant.
- Лица, добавленные до 2.0.9 без связи с Person, не теряются: добавлен отдельный пункт «Связать лицо с человеком Home Assistant» без повторной загрузки фотографии или пересчёта descriptor.
- Recognition worker использует внутренний стабильный identity key, поэтому одинаковые friendly name разных Person не смешиваются.
- События `face_recognized` и сенсор «Последний посетитель» теперь дополнительно публикуют `person_entity_id`; поле `person` сохранено для обратной совместимости.
- Action `intersvyaz.add_known_face` получил необязательный `person_entity_id`; старый сценарий с `name` продолжает работать.
- Диагностика показывает только количество связанных Person и не раскрывает сами entity_id или биометрические descriptors.
- Добавлено подробное stage-логирование регистрации/сопоставления лиц без вывода фотографий и descriptor.

## 2.0.8

- Добавлен отдельный клиент камер `cams.is74.ru/api/yard-with-group`.
- Home Assistant теперь создаёт камеры для всех доступных подъездов/дворов с `ACCESS.LIVE.STATUS=true`.
- Добавлена поддержка HLS live stream через `MEDIA.HLS.LIVE.MAIN`, а snapshot берётся из `MEDIA.SNAPSHOT.LIVE.MAIN`.
- Основная камера сопоставляется с существующим relay-домофоном по адресу/подъезду и сохраняет прежний `unique_id`, чтобы не создавать дубль сущности.
- Камеры без доступной функции открытия создаются как отдельные camera-only устройства.
- Добавлен `YardCameraManager`: обновление временных media URL, безопасный fallback и reload при изменении состава камер.
- Все cameras API URL с bearer-параметрами остаются только в runtime и не выводятся в diagnostics/log.
- Фоновое распознавание теперь может использовать любую доступную yard-камеру; для camera-only источников auto-open принципиально недоступен.
- Добавлены EventEntity и сенсор последнего посетителя для camera-only камер.
- Добавлено подробное логирование `[YARD_CAMERAS][...]` без раскрытия адресов, UUID и токенов.

## 2.0.7

- Полностью удалены `opencv-python-headless` и `opencv-contrib-python-headless`. Причина: официальный Home Assistant Container/HA OS основан на Alpine/musl, а OpenCV публикует Linux wheels в формате manylinux/glibc; Home Assistant поэтому выбирал sdist и пытался собирать OpenCV внутри контейнера.
- Новый portable recognition worker использует только Pillow (уже базовая зависимость Home Assistant) и `numpy==2.3.2`. Home Assistant отдельно фиксирует NumPy 2.3.2 в package constraints как musllinux-совместимый пакет.
- Реализован lightweight face-like detector по консервативной skin/geometry маске без внешних моделей и без системных библиотек.
- Новый 128-мерный `portable_face_v1` descriptor объединяет нормализованную яркость и карту градиентов и устойчив к зеркальному отражению камеры.
- Порог нового движка по умолчанию снижен до `0.30`; для auto-open теперь по умолчанию требуется 3 последовательных совпадения.
- Диапазон порога в UI синхронизирован с portable engine: `0.10..0.55`; старое значение вне диапазона больше не ломает форму настроек.
- ConfigEntry schema поднята до v4: при переходе со старого dlib/OpenCV engine автоматическое открытие принудительно переводится в безопасный `observe`, пока пользователь заново не добавит лица и явно не включит auto-open.
- Auto-open дополнительно блокируется, если portable worker считает найденный кандидат недостаточно надёжным; режим `observe` остаётся режимом по умолчанию.
- Descriptors dlib/OpenCV намеренно не мигрируются в portable format: известных людей после обновления необходимо добавить заново.
- Добавлены healthcheck и end-to-end smoke tests portable worker; regression-тест запрещает возврат OpenCV/dlib в manifest.

## 2.0.6

- Исправлена установка OpenCV в Home Assistant 2026.8 / Python 3.14 на Linux: `opencv-python-headless==4.14.0.94` заменён на `opencv-contrib-python-headless==4.14.0.94`.
- Для `opencv-contrib-python-headless` опубликованы готовые ABI3 manylinux wheels для x86-64 и ARM64, поэтому Home Assistant не должен пытаться собирать OpenCV из исходников.
- Убран отдельный pin `numpy==2.3.2`: совместимую версию NumPy теперь разрешает штатная зависимость OpenCV, что снижает риск конфликтов с окружением Home Assistant.
- Логика распознавания не менялась: используется тот же `cv2`, Haar-детектор и LBP descriptor; меняется только корректно устанавливаемый бинарный пакет.

## 2.0.5

Замена несовместимого на части старых CPU dlib-движка распознавания.

- Полностью удалены runtime-зависимости `dlib-bin` и `face-recognition-models`: они вызывали `SIGILL` на старом процессоре и делали добавление лиц невозможным.
- Новый локальный движок был переведён на `opencv-python-headless==4.14.0.94`; на части Linux/Python 3.14 окружений Home Assistant не находил подходящий wheel и пытался собирать пакет из исходников. Это исправлено в 2.0.6 переходом на `opencv-contrib-python-headless`.
- Распознавание остаётся изолированным в отдельном worker-процессе: `cv2`/`numpy` не импортируются процессом Home Assistant.
- Реализован встроенный pipeline Haar face detector + 128-мерный spatial LBP descriptor без скачивания внешних моделей.
- Worker ограничен одним OpenCV thread и OpenCL отключён, чтобы не создавать лишнюю нагрузку на слабые Home Assistant хосты.
- Descriptor теперь содержит `face_engine=opencv_lbp_v1`. Старые dlib descriptors намеренно не используются, чтобы исключить опасные ложные совпадения; после обновления старые лица нужно добавить заново.
- Сохранён прежний формат из 128 чисел и прежняя шкала 0..1 `distance`, поэтому events/options не ломают внешние автоматизации.
- Обновлены тексты настроек и документация: порог больше не называется dlib distance.
- Добавлены regression-тесты, запрещающие возвращение dlib в manifest/worker и проверяющие маркировку descriptors движком.

## 2.0.4

Исправление пустых пунктов меню в настройках интеграции на Home Assistant 2026.8.

- Переводы меню options flow переведены на актуальную схему Home Assistant: `menu_options` вместо устаревшего вложенного `menu.options`.
- Добавлены пояснения `menu_option_descriptions` для всех пунктов настроек распознавания.
- В сводке настроек внутренний режим `observe/auto_open/off` теперь показывается человекочитаемо на RU/EN.
- Добавлен regression-тест структуры переводов меню, чтобы пустые строки в UI не вернулись.

## 2.0.3

Исправление совместимости сенсора баланса с Home Assistant 2026.8.

- Удалён импорт `UnitOfCurrency`, которого нет в `homeassistant.const` текущего Home Assistant.
- Денежный сенсор теперь использует строковый ISO 4217 код `RUB`, как требует `SensorDeviceClass.MONETARY`.
- Добавлен regression-тест, запрещающий повторное использование `UnitOfCurrency`.

## 2.0.2

Исправление мастера настройки после выбора договора.

- Исправлен `NameError: CONF_DOOR_MAC is not defined` при создании ConfigEntry после успешной авторизации и выбора договора.
- Добавлены явные stage-логи config flow: выбор договора, получение mobile token, загрузка домофонов, CRM-авторизация и создание записи.
- Добавлен статический тест, который проверяет, что все используемые в `config_flow.py` константы верхнего регистра импортированы или определены, чтобы подобная ошибка не повторилась.

## 2.0.1

Аварийное исправление стабильности для старых CPU и несовместимых native wheels.

- Нативный `dlib` больше никогда не импортируется в процессе Home Assistant.
- Распознавание вынесено в отдельный постоянный worker-процесс с JSONL IPC.
- `SIGILL`, `SIGSEGV` и `SIGBUS` в dlib worker больше не перезапускают Home Assistant: падает только worker, а интеграция продолжает работать без распознавания.
- После fatal native crash распознавание отключается до следующего перезапуска, чтобы не создавать бесконечный цикл падений worker.
- Worker загружает модели один раз и переиспользуется между кадрами; отдельный процесс не создаётся на каждый snapshot.
- При выгрузке интеграции worker корректно завершается.
- Добавлено подробное логирование запуска, RPC, timeout и причины аварийного завершения worker без передачи изображений/биометрических данных в журнал.
- Исправлена критическая проблема 2.0.0, из-за которой открытие config flow на CPU, несовместимом с wheel `dlib-bin`, могло завершать Home Assistant сигналом `SIGILL`.

## 2.0.0


Крупное архитектурное обновление самостоятельной Home Assistant интеграции.

### Home Assistant
- Минимальная поддерживаемая версия: Home Assistant 2026.8.
- Runtime переведён на типизированный `ConfigEntry.runtime_data` вместо большого `hass.data`.
- Добавлены штатные re-auth и reconfigure flows.
- Удалён config-entry update listener, чтобы не конфликтовать с reload helpers новых Home Assistant.
- Каждый физический домофон теперь представлен отдельным HA Device; аккаунт остаётся hub/service device.
- Добавлена EventEntity для каждого домофона.
- Добавлена безопасная диагностика.

### Домофоны и камеры
- Новый `DoorManager` отвечает за обнаружение, fallback, обновление временных ссылок и открытие.
- Основные и расшаренные домофоны по-прежнему объединяются с дедупликацией.
- При изменении состава домофонов entry автоматически перезагружается для актуализации сущностей.
- Новый единый `DoorSnapshotManager` исключает лишние параллельные загрузки одного кадра.
- Камера при протухшей ссылке выполняет одно обновление данных домофона и повторяет запрос.

### Распознавание лиц
- Убрана зависимость от вручную устанавливаемого `face_recognition`.
- Используется локальный `dlib-bin==20.0.1` + `face-recognition-models==0.3.0`, объявленные в manifest requirements.
- Сохранена совместимость со старыми 128-мерными dlib descriptors.
- Добавлены режимы `off`, `observe`, `auto_open`.
- Новые установки используют `observe` и два последовательных совпадения.
- Для legacy-entry с уже существующими лицами миграция сохраняет прежнее auto-open поведение и одно совпадение.
- Добавлены настраиваемые threshold, required matches, auto-open cooldown и event cooldown.
- Идентичные кадры дедуплицируются по hash.
- `unknown_person` теперь подчиняется event cooldown и не спамит событием на каждом новом кадре.
- Автооткрытие блокируется, если на подтверждающем кадре обнаружено не ровно одно лицо.
- Ошибка физического открытия больше не ломает выдачу camera snapshot/фоновый цикл распознавания.

### События
- Добавлены event types: `face_recognized`, `unknown_person`, `door_opened`, `door_open_failed`.
- Сохранены удобные event-bus события: `intersvyaz_face_recognized`, `intersvyaz_unknown_person`, `intersvyaz_door_opened`, `intersvyaz_door_open_failed`.
- Добавлен сенсор «Последний посетитель».
- Исправлен сценарий из issue #17: больше не нужно вручную редактировать Python-код, чтобы строить уведомления по распознаванию.

### API и безопасность
- Исправлена ошибка, при которой отсутствующий `TOKEN` мог превращаться в строку `"None"` и считаться валидным.
- Выделены `IntersvyazAuthError` и `IntersvyazNetworkError`.
- HTTP 401/403 теперь корректно запускают re-auth.
- Credentials больше не попадают в coordinator data.
- Добавлен единый рекурсивный sanitizer для request context, диагностики и ошибок.
- Runtime UID домофона (в legacy-формате он содержит MAC) не выводится в лог: используется короткая hash-ссылка.
- Debug-summary ответов API больше не печатает значения неизвестных полей, только ключи/размер структуры.
- Убрано намеренное логирование сырых token/config payload.

### Код и поддержка
- Большая часть runtime/service/recognition логики вынесена из `__init__.py` в небольшие модули.
- Options flow отделён от config flow.
- Services/actions вынесены в отдельный модуль и регистрируются в `async_setup`, независимо от состояния ConfigEntry.
- Ошибки неправильного использования actions переведены на `ServiceValidationError` с переводимыми сообщениями.
- Entity names переведены на Home Assistant translation keys (RU/EN).
- README полностью обновлён: HACS-кнопка, установка, deployment types, recognition, events, re-auth, diagnostics и troubleshooting.
- Добавлен CI с compile/tests, HACS validation и hassfest.
