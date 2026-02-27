#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag


INDEX_URL = "https://reshak.ru/reshebniki/geometriya/10/atanasyan10-11/index.php"
PROBLEM_URL = "https://reshak.ru/otvet/reshebniki.php?otvet=new/{num}&predmet=atan10_11"

SCRIPT_DIR = Path(__file__).resolve().parent
SOLUTIONS_DIR = SCRIPT_DIR / "solutions"
OUTPUT_ROOT = SOLUTIONS_DIR / "Geometry"
METADATA_PATH = SOLUTIONS_DIR / "metadata_geometry.json"

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REQUEST_TIMEOUT = 60
REQUEST_INTERVAL = 1.5
MAX_RETRIES = 3

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif")

LAST_REQUEST_TS = 0.0


def rate_limit() -> None:
    global LAST_REQUEST_TS
    delta = time.time() - LAST_REQUEST_TS
    if delta < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - delta)
    LAST_REQUEST_TS = time.time()


def get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.7,en;q=0.6",
        }
    )
    return session


def request_with_retries(session: requests.Session, url: str) -> Optional[requests.Response]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            rate_limit()
            return session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException:
            if attempt < MAX_RETRIES:
                time.sleep(1.0)
    return None


def parse_problem_numbers(session: requests.Session) -> List[int]:
    response = request_with_retries(session, INDEX_URL)
    if response is None or response.status_code != 200:
        return []

    response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")

    numbers: List[int] = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        marker = "otvet=new/"
        subject = "&predmet=atan10_11"
        if marker not in href or subject not in href:
            continue
        start = href.find(marker) + len(marker)
        end = href.find(subject, start)
        if end <= start:
            continue
        raw = href[start:end]
        if raw.isdigit():
            numbers.append(int(raw))

    return sorted(set(numbers))


def pick_image_url(img: Tag, page_url: str) -> Optional[str]:
    for attr in ("src", "data-src", "data-original", "data-lazy", "data-srcset"):
        val = img.get(attr)
        if not val:
            continue
        if attr == "data-srcset":
            val = val.split(",")[0].strip().split(" ")[0]
        full = urljoin(page_url, val)
        if full.lower().split("?", 1)[0].endswith(IMAGE_EXTENSIONS):
            return full
    return None


def extract_solution1_images(html: str, page_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    urls: List[str] = []

    block = soup.select_one("div.pic_otvet1")
    if block is not None:
        for img in block.select("img"):
            url = pick_image_url(img, page_url)
            if url and url not in urls:
                urls.append(url)
        if urls:
            return urls

    start_node = None
    end_markers = ("Решение #2", "Решение 2", "Решение №2")
    for text_node in soup.find_all(string=True):
        txt = str(text_node)
        if "Решение #1" in txt or "Решение 1" in txt or "Решение №1" in txt:
            start_node = text_node
            break

    if start_node is None:
        return urls

    for el in start_node.parent.next_elements if start_node.parent else []:
        if isinstance(el, NavigableString):
            if any(marker in str(el) for marker in end_markers):
                break
            continue
        if not isinstance(el, Tag) or el.name != "img":
            continue
        url = pick_image_url(el, page_url)
        if url and url not in urls:
            urls.append(url)

    return urls


def download_image(session: requests.Session, url: str, filepath: Path) -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            rate_limit()
            response = session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code != 200:
                if attempt < MAX_RETRIES:
                    time.sleep(1.0)
                continue
            data = response.content
            if len(data) < 200:
                if attempt < MAX_RETRIES:
                    time.sleep(1.0)
                continue
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_bytes(data)
            return True
        except requests.RequestException:
            if attempt < MAX_RETRIES:
                time.sleep(1.0)
    return False


def download_problem(problem_num: int, session: requests.Session, output_root: Path) -> Tuple[int, Optional[str]]:
    page_url = PROBLEM_URL.format(num=problem_num)
    response = request_with_retries(session, page_url)
    if response is None:
        return 0, "network error"
    if response.status_code == 404:
        return 0, None
    if response.status_code != 200:
        return 0, f"http {response.status_code}"

    response.encoding = "utf-8"
    image_urls = extract_solution1_images(response.text, page_url)
    if not image_urls:
        return 0, None

    task_dir = output_root / str(problem_num)
    if task_dir.exists():
        for old in task_dir.glob("*"):
            if old.is_file():
                old.unlink()

    downloaded = 0
    for idx, img_url in enumerate(image_urls, start=1):
        ext = Path(img_url.split("?")[0]).suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            ext = ".png"
        if download_image(session, img_url, task_dir / f"{idx}{ext}"):
            downloaded += 1

    if downloaded == 0 and task_dir.exists():
        try:
            task_dir.rmdir()
        except OSError:
            pass

    return downloaded, None


def collect_file_stats(task_dir: Path) -> Tuple[List[str], float]:
    files: List[str] = []
    total_size_mb = 0.0
    for file in sorted(task_dir.glob("*")):
        if not file.is_file():
            continue
        files.append(file.name)
        total_size_mb += file.stat().st_size / (1024 * 1024)
    return files, round(total_size_mb, 3)


def write_metadata(records: Dict[str, dict], output_root: Path) -> None:
    total_images = sum(item["images_count"] for item in records.values())
    total_size_mb = sum(item["size_mb"] for item in records.values())
    payload = {
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "subject": "geometry",
        "textbook": "atanasyan10-11",
        "source": "reshak.ru",
        "solution_type": "solution_1_only",
        "output_folder": str(output_root),
        "problems": records,
        "total_problems": len(records),
        "total_images": total_images,
        "total_size_mb": round(total_size_mb, 3),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    METADATA_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Download only 'Решение #1' images for geometry (Atanasyan 10-11) from reshak.ru"
    )
    parser.add_argument("--start", type=int, default=1, help="First problem number")
    parser.add_argument("--end", type=int, default=None, help="Last problem number (optional)")
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT, help="Target output folder")
    args = parser.parse_args()

    if args.start < 1:
        print("Invalid range. Use: --start N where N > 0")
        return 1
    if args.end is not None and (args.end < 1 or args.start > args.end):
        print("Invalid range. Use: --start N --end M with N <= M and N > 0")
        return 1

    session = get_session()
    available_numbers = parse_problem_numbers(session)
    if available_numbers:
        if args.end is None:
            targets = [n for n in available_numbers if n >= args.start]
        else:
            targets = [n for n in available_numbers if args.start <= n <= args.end]
    else:
        if args.end is None:
            print("Could not read index list, please provide --end explicitly.")
            return 1
        targets = list(range(args.start, args.end + 1))

    if not targets:
        print("No numbers found in this range.")
        return 0

    print(f"Source: {INDEX_URL}")
    print(f"Range: {args.start}-max_available" if args.end is None else f"Range: {args.start}-{args.end}")
    print(f"Targets: {len(targets)}")
    print(f"Output: {args.output.resolve()}")

    stats = {"ok": 0, "empty": 0, "error": 0, "images": 0}
    records: Dict[str, dict] = {}

    for idx, num in enumerate(targets, start=1):
        downloaded, error = download_problem(num, session, args.output)
        if downloaded > 0:
            stats["ok"] += 1
            stats["images"] += downloaded
            task_dir = args.output / str(num)
            files, size_mb = collect_file_stats(task_dir)
            records[str(num)] = {
                "images_count": downloaded,
                "image_files": files,
                "size_mb": size_mb,
                "downloaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "page_url": PROBLEM_URL.format(num=num),
            }
            print(f"[{idx}/{len(targets)}] {num}: OK ({downloaded} img)")
        elif error:
            stats["error"] += 1
            print(f"[{idx}/{len(targets)}] {num}: ERROR ({error})")
        else:
            stats["empty"] += 1
            print(f"[{idx}/{len(targets)}] {num}: EMPTY")

    write_metadata(records, args.output)
    print("")
    print("Done.")
    print(f"Problems with images: {stats['ok']}")
    print(f"Total images: {stats['images']}")
    print(f"Empty: {stats['empty']}")
    print(f"Errors: {stats['error']}")
    print(f"Metadata: {METADATA_PATH.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
