#!/usr/bin/env python3
# coding: utf-8

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from PIL import Image


BASE_DIR_URL = "https://reshak.ru/reshebniki/geometriya/10/atanasyan10-11"
INDEX_URL = f"{BASE_DIR_URL}/index.php"
PROBLEM_URL = f"{BASE_DIR_URL}/{{num}}.php"
OUTPUT_ROOT = Path("solutions") / "geometry"
METADATA_PATH = Path("solutions") / "metadata.json"
METADATA_GEOMETRY_PATH = Path("solutions") / "metadata_geometry.json"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

REQUEST_INTERVAL = 5.0
IMAGE_RETRIES = 3

# Баннеры и мусор для исключения
BAN_KEYWORDS = [
    "vichy", "ozon", "dercos", "banner", "ad", "advertisement",
    "logo", "icon", "favicon", "pixel", "transparent", "1x1",
    "яндекс", "yandex", "google", "praticsum", "lms"
]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

LAST_REQUEST = 0.0


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(message)s'
    )


logger = logging.getLogger(__name__)


def rate_limit():
    """Ограничить частоту запросов"""
    global LAST_REQUEST
    elapsed = time.time() - LAST_REQUEST
    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)
    LAST_REQUEST = time.time()


def get_session():
    """Создать сессию с браузерными заголовками"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
    })
    return session


def is_banned_url(url: str) -> bool:
    """Проверить что это баннер/реклама"""
    lower = url.lower()
    return any(keyword in lower for keyword in BAN_KEYWORDS)


def is_image_url(url: str) -> bool:
    """Проверить что это изображение"""
    path = urlparse(url).path.lower()
    _, ext = os.path.splitext(path)
    return ext in IMAGE_EXTENSIONS


def get_images_between_markers(html_content: str, page_url: str) -> list:
    """
    Берет ТОЛЬКО изображения между маркерами "Решение #1" и "Решение #2"
    (или до конца, если "Решение #2" не найдено).
    """
    soup = BeautifulSoup(html_content, "html.parser")

    start_marker = "Решение #1"
    end_marker = "Решение #2"

    start_nodes = soup.find_all(string=lambda s: s and start_marker in s)
    if not start_nodes:
        return []

    start_node = start_nodes[0]
    start_container = start_node.parent if isinstance(start_node, NavigableString) else None
    if not start_container:
        return []

    images = []
    for el in start_container.next_elements:
        if isinstance(el, NavigableString) and end_marker in el:
            break
        if not isinstance(el, Tag):
            continue
        if el.name != "img":
            continue

        url = None
        for attr in ("src", "data-src", "data-original", "data-lazy", "data-srcset"):
            val = el.get(attr)
            if not val:
                continue
            if attr == "data-srcset":
                val = val.split(",")[0].strip().split(" ")[0]
            url = val
            break
        if not url:
            continue

        full_url = urljoin(page_url, url)
        if is_banned_url(full_url):
            continue
        if not is_image_url(full_url):
            continue
        if full_url not in images:
            images.append(full_url)

    return images


def get_problem_numbers(session: requests.Session) -> list:
    """Получить номера задач из index.php в порядке на странице."""
    rate_limit()
    response = session.get(INDEX_URL, timeout=30)
    if response.status_code != 200:
        return []

    response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    numbers = []
    for a in soup.find_all("a"):
        href = a.get("href")
        if not href:
            continue
        href = href.strip()
        m = re.search(r"/?(\d+)\.php$", href)
        if not m:
            continue
        numbers.append(int(m.group(1)))

    seen = set()
    ordered = []
    for n in numbers:
        if n in seen:
            continue
        seen.add(n)
        ordered.append(n)
    return ordered


def download_image(session: requests.Session, url: str, filepath: Path) -> bool:
    """Скачать одно изображение с проверкой"""
    for attempt in range(1, IMAGE_RETRIES + 1):
        try:
            rate_limit()
            response = session.get(url, timeout=30)
            
            if response.status_code != 200:
                if attempt < IMAGE_RETRIES:
                    time.sleep(1)
                continue
            
            data = response.content
            if len(data) < 100:  # Слишком маленькое
                if attempt < IMAGE_RETRIES:
                    time.sleep(1)
                continue
            
            # Проверяем что это изображение с PIL
            try:
                with Image.open(io.BytesIO(data)) as img:
                    img = img.convert("RGB")
                    filepath.parent.mkdir(parents=True, exist_ok=True)
                    img.save(filepath, format="PNG")
                return True
            except Exception as e:
                if attempt < IMAGE_RETRIES:
                    time.sleep(1)
                continue
        
        except Exception as e:
            if attempt < IMAGE_RETRIES:
                time.sleep(1)
                continue
    
    return False


def download_problem(problem_num: int, session: requests.Session) -> tuple:
    """
    Скачать все фото для одного номера
    Возвращает (количество_скачано, ошибка_строкой_или_None)
    """
    url = PROBLEM_URL.format(num=problem_num)
    
    try:
        rate_limit()
        response = session.get(url, timeout=20)
        
        if response.status_code == 404:
            return (0, None)

        if response.status_code != 200:
            return (0, f"Ошибка HTTP {response.status_code}")

        # ✅ ИСПРАВЛЕНО: правильная кодировка
        response.encoding = "utf-8"
        
        # Берем ТОЛЬКО Решение #1
        image_urls = get_images_between_markers(response.text, url)
        
        if not image_urls:
            return (0, None)
        
        # Скачиваем каждое фото
        dest_dir = OUTPUT_ROOT / str(problem_num)
        downloaded = 0
        
        for idx, img_url in enumerate(image_urls, 1):
            filepath = dest_dir / f"{idx}.png"
            
            if download_image(session, img_url, filepath):
                downloaded += 1
            else:
                # Удаляем неполный файл
                if filepath.exists():
                    try:
                        filepath.unlink()
                    except:
                        pass
        
        # Если ничего не скачалось - удаляем папку
        if downloaded == 0:
            if dest_dir.exists():
                try:
                    for f in dest_dir.iterdir():
                        f.unlink()
                    dest_dir.rmdir()
                except:
                    pass
            return (0, None)
        
        return (downloaded, None)
    
    except Exception as e:
        return (0, str(e))


def write_metadata(records: dict) -> Path:
    total_images = sum(v["images_count"] for v in records.values())
    total_size = sum(v["size_mb"] for v in records.values())
    payload = {
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "subject": "geometry",
        "grade": "10-11",
        "textbook": "atanasyan",
        "problems": records,
        "total_images": total_images,
        "total_size_mb": round(total_size, 2),
    }

    target = METADATA_PATH
    if METADATA_PATH.exists():
        try:
            with METADATA_PATH.open("r", encoding="utf-8") as f:
                head = f.read(2000)
            if "\"subject\": \"algebra\"" in head:
                target = METADATA_GEOMETRY_PATH
        except Exception:
            target = METADATA_GEOMETRY_PATH

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return target


def main():
    parser = argparse.ArgumentParser(description="Скачивание геометрии (Атанасян) с reshak.ru")
    parser.add_argument("--start", type=int, default=1, help="Первый номер (по умолчанию 1)")
    parser.add_argument("--end", type=int, default=1000, help="Последний номер (по умолчанию 1000)")
    args = parser.parse_args()
    
    # Проверка параметров
    if args.start < 1 or args.end < 1 or args.start > args.end:
        print("❌ Ошибка: некорректный диапазон номеров!")
        return 1
    
    setup_logging()
    
    print("")
    print("="*70)
    print("📥 СКАЧИВАНИЕ РЕШЕНИЙ ГЕОМЕТРИЯ (Атанасян 10-11 класс)")
    print("="*70)
    print(f"Диапазон: {args.start} - {args.end} (всего {args.end - args.start + 1} номеров)")
    print(f"Папка назначения: {OUTPUT_ROOT.absolute()}")
    print(f"Задержка: {REQUEST_INTERVAL} сек между запросами")
    print("="*70)
    print("")
    
    session = get_session()
    
    start_time = time.time()
    stats = {
        "success": 0,
        "empty": 0,
        "error": 0,
        "total_images": 0,
    }
    
    problem_numbers = get_problem_numbers(session)
    if not problem_numbers:
        problem_numbers = list(range(args.start, args.end + 1))

    # Фильтруем по диапазону
    problem_numbers = [n for n in problem_numbers if args.start <= n <= args.end]
    total = len(problem_numbers)

    metadata_records = {}

    for idx, num in enumerate(problem_numbers, 1):
        downloaded, error = download_problem(num, session)
        
        if downloaded > 0:
            stats["success"] += 1
            stats["total_images"] += downloaded
            print(f"[{idx:4d}/{total}] Номер {num:4d}... ✅ Скачано {downloaded} фото")

            dest_dir = OUTPUT_ROOT / str(num)
            size_mb = 0.0
            image_files = []
            if dest_dir.exists():
                for f in sorted(dest_dir.iterdir()):
                    if f.is_file():
                        image_files.append(f.name)
                        size_mb += f.stat().st_size / (1024 * 1024)

            metadata_records[str(num)] = {
                "images_count": downloaded,
                "image_files": image_files,
                "size_mb": round(size_mb, 2),
                "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        elif error:
            stats["error"] += 1
            print(f"[{idx:4d}/{total}] Номер {num:4d}... ❌ {error}")
        else:
            stats["empty"] += 1
            print(f"[{idx:4d}/{total}] Номер {num:4d}... ⏭️ Решение не найдено")
    
    elapsed = time.time() - start_time
    hours = int(elapsed // 3600)
    minutes = int((elapsed % 3600) // 60)
    seconds = int(elapsed % 60)
    
    # Размер на диске
    total_size = 0
    if OUTPUT_ROOT.exists():
        for root, dirs, files in os.walk(OUTPUT_ROOT):
            for f in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, f))
                except:
                    pass
    
    size_mb = total_size / (1024 * 1024)
    
    print("")
    print("="*70)
    print("✅ СКАЧИВАНИЕ ЗАВЕРШЕНО!")
    print("="*70)
    print(f"✅ Успешно скачано: {stats['success']} номеров")
    print(f"📊 Всего фото: {stats['total_images']}")
    print(f"⏭️  Пусто (нет решений): {stats['empty']}")
    print(f"⏱️  Время выполнения: {hours}ч {minutes}м {seconds}с")
    print(f"💾 Размер на диске: {size_mb:.2f} МБ")
    if metadata_records:
        metadata_file = write_metadata(metadata_records)
        print(f"🧾 Metadata: {metadata_file}")
    print("="*70)
    print("")
    
    if stats['total_images'] > 0:
        print("✅ Геометрия успешно скачана!")
        print("\n💡 Следующие шаги:")
        print("   1. python clean_purple_images.py (очистить баннеры)")
        print("   2. python import_solutions_to_db.py (загрузить в БД)")
        print("")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
