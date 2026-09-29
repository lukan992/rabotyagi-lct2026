# Модуль приблизительных пересечений камер

Самостоятельный Python-пакет `lct-camera-overlap` (`import camera_overlap`), версия 0.1.0. Получает два фото и предлагает общие области на обоих кадрах по SuperPoint + LightGlue с геометрической проверкой. Работает локально, без API-ключей, БД, калибровки и папки исследовательских экспериментов. Два проверенных файла весов включены в пакет, SHA-256 проверяется при загрузке. Обучение/дообучение не требуется.

## Установка

Python 3.11–3.13. Зафиксированы проверенные зависимости: PyTorch 2.7.1, NumPy 2.1.2, Pillow 12.2.0, Kornia 0.8.3, OpenCV 5.0.0.93. Версия сборки PyTorch/CUDA должна подходить устройству; текущие CUDA-проверки выполнены на RTX 5090 с `torch==2.7.1+cu128`. Для CPU выбирайте `--device cpu`.

Из корня проекта:

```powershell
python -m pip install ./camera_overlap_module
```

Пакет пересечений устанавливается отдельно. Для wheel используйте `python -m pip wheel --no-deps ./camera_overlap_module`. Скачивание Python-зависимостей при установке возможно; **при анализе фото сетевых запросов нет**.

## HTTP-сервис и использование в «СтройКонтроле»

Из `Hakaton-main` выполните `docker compose up --build`: внутренний сервис `camera-overlap` загрузит модель один раз и примет пары JPEG от backend на `POST /analyze` (multipart-поля `image0`, `image1`). `GET /health/live` проверяет работу HTTP-процесса. При установленном `OVERLAP_SERVICE_TOKEN` запрос должен содержать совпадающий заголовок `X-Overlap-Token`; тот же токен задаётся backend через `SK_OVERLAP_SERVICE_TOKEN`.

Backend выбирает сохранённые снимки двух активных камер одного объекта, отправляет их сервису, сохраняет JSON результата в `camera_overlaps` и переиспользует его до изменения RTSP-источника или явного пересчёта. `GET /api/sites/{site_id}/camera-overlaps` возвращает квадратную матрицу статусов и геометрию пар. `POST /api/sites/{site_id}/camera-overlaps/recompute` пересчитывает пару по запросу руководителя или администратора. Приблизительное число техники доступно в `GET /api/sites/{site_id}/equipment-check`: `estimatedObserved` для рабочей зоны, `estimatedSiteObserved` для всего объекта. В интерфейсе оценки помечены «неточно».

## Python-интерфейс

```python
from camera_overlap import OverlapDetector

detector = OverlapDetector(device="auto")  # загрузить один раз
result = detector.analyze("camera_a.jpg", "camera_b.jpg")

print(result.status)                      # candidate_overlap / insufficient_evidence
payload = result.to_dict()                # JSON-совместимые метрики и контуры
regions_a = payload["images"][0]["regions"]
regions_b = payload["images"][1]["regions"]
mask_a = result.mask_original(0)          # numpy uint8, 0/255, размер первого фото
mask_b = result.mask_original(1)
result.save("outputs/my_overlap")         # result.json, маски, NPZ, превью
```

`analyze(image0, image1)` также принимает закодированные `bytes`/`bytearray` и `PIL.Image.Image`, поэтому HTTP-обработчик передаёт два файла непосредственно из запроса. Одна пара на вызов; на одном экземпляре вызовы сериализованы. Модели загружаются в конструкторе и переиспользуются. HTTP-адаптер `service.py` выполняет вычисление в отдельном потоке.

При недостатке данных возвращается обычный результат с пустыми масками/контурами и причинами отказа. Ошибки ввода/весов/устройства — `InvalidImageError`, `ModelWeightsError`, `InferenceError` (общий базовый тип `CameraOverlapError`); фиктивного результата при ошибке нет. Повторный `save` в непустую папку требует явного `overwrite=True`.

## CLI

```powershell
camera-overlap camera_a.jpg camera_b.jpg --output outputs/my_overlap --device auto
# Эквивалент, если entrypoint отсутствует в PATH:
python -m camera_overlap camera_a.jpg camera_b.jpg --output outputs/my_overlap
```

CLI печатает краткий JSON. Exit 0 означает выполненный анализ, включая `insufficient_evidence`; exit 2 означает ошибку. Есть `--weights-dir` для двух файлов с теми же проверенными SHA-256, `--overwrite` для явного перезаписывания результатов и `--assume-planar` только для заведомо общей плоскости.

## Вход и координаты

- JPEG, PNG, WEBP, PPM, BMP и однокадровый TIFF; пути Windows с Unicode поддержаны. Многостраничные/анимированные изображения отклоняются.
- По умолчанию максимум 50 MiB закодированного файла и 40 млн пикселей; минимум 32 px по каждой стороне и 16 px по короткой стороне после resize. Ограничения можно менять в `OverlapConfig`.
- EXIF-поворот применяется; прозрачность сводится на белый фон, далее RGB. Размеры, точки и контуры относятся к **пикселям фото после EXIF-поворота**. Для камер без EXIF это исходные координаты. Это не мировые координаты, метры или план участка.
- `result.masks` — две компактные маски, длинная сторона около 512 px, uint8 0/255. `mask_original(i)` разворачивает их в размер фото. Сохранённые `mask0.png`/`mask1.png` компактные; размер/масштаб есть в JSON.
- Каждый элемент `images[i].regions` содержит `exterior` и `holes`: списки `[x,y]` в координатах фото. Контуры упрощены; точнее растровая маска. Площадь маски не является измеренной площадью покрытия на земле.
- `matches.npz` хранит `points0`, `points1`, `scores`, `selected_inliers`, `F_inliers`, `H_inliers`. LightGlue scores — оценки соответствий, не вероятность правильности всей зоны.

JSON-контракт: [src/camera_overlap/schemas/result.schema.json](src/camera_overlap/schemas/result.schema.json). Корневые поля: `schema_version=camera-overlap-v1`, `module_version`, `model`, `status`, `images`, `geometry`, `config`, `weights_sha256`, `device`, `elapsed_seconds`, пояснение/ограничения. После `save` добавляется `artifacts` с относительными именами файлов.

## Алгоритм и настройки

По умолчанию до 2048 SuperPoint-признаков при длинной стороне 1024; LightGlue с проверенными SuperPoint-весами и прежними adaptive settings. Затем F/H USAC_MAGSAC (2/3 px при масштабе 1024), ≥50 inliers, доля ≥0.45, оболочка ≥1% в обоих кадрах. Для объёмной сцены область строится локальными треугольниками и окрестностями совпавших точек; весь кадр по H не переносится.

Если вызывающий код **знает, что сцена плоская**, допускается:

```python
from camera_overlap import OverlapConfig, OverlapDetector
detector = OverlapDetector(config=OverlapConfig(assume_planar=True))
```

Перенос границ плоскости дополнительно требует ≥50 H-inliers, долю ≥0.6, оболочку ≥10% и невырожденную H. Совпавший фасад не означает, что стройплощадка плоская: для обычных камер оставляйте `assume_planar=False`. F в JSON дана и в координатах фото (`F_original_pixels`), и в нормализованных (`F_normalized` с `geometry_long_edge`); H переносит координаты первого фото во второе.

## Границы результата

`candidate_overlap` — **предложение приблизительной общей видимой области**, требующее проверки, а не подтверждение полного пересечения покрытия. Повторяющиеся фасады могут пройти оценённую F/H и дать ложную область. `insufficient_evidence` не доказывает отсутствия общей зоны. Сегментация земли/техники, коррекция дисторсии неизвестных камер, восстановление 3D, автоматическая калибровка и преобразование в координаты участка не реализованы.

Общий фильтр эвристический; независимая точность на стационарных камерах стройки пока не измерена. Известны ошибки на повторяющихся структурах.

## Происхождение

LightGlue закреплён на `eb42fee2d71449efb0aa5c10549752b5d75384d8`. Выбранные исходники включены в `_vendor`, исходные лицензия и per-file notices сохранены; единственное изменение SuperPoint — обязательная загрузка локального checkpoint вместо URL. LightGlue создаётся без автоматической загрузки, ключи checkpoint преобразуются по upstream-процедуре; архитектура не меняется. Происхождение/хеши: `_vendor/provenance.json`, `_vendor/NOTICE.txt`, `weights/manifest.json`. Пакет не меняет `torch.hub` cache, глобальные функции скачивания, число потоков или seed PyTorch.
