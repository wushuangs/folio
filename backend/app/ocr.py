import json
import os
import platform
import shutil
import subprocess
import tempfile
import hashlib
import threading
from pathlib import Path

_compile_lock = threading.Lock()


def capabilities():
    if shutil.which('tesseract'):
        return {'available': True, 'engine': 'tesseract'}
    if platform.system() == 'Darwin' and shutil.which('swiftc'):
        return {'available': True, 'engine': 'apple-vision'}
    return {'available': False, 'engine': None}


def recognize(image):
    if image.width * image.height > 40_000_000:
        raise ValueError('图片超过 4000 万像素，请缩小后再识别。')
    engine = capabilities()['engine']
    if not engine:
        raise ValueError('本机缺少 OCR 引擎。请安装 Tesseract 与 chi_sim/eng 语言包，或使用包含 OCR 的 Docker 镜像。')
    with tempfile.TemporaryDirectory(prefix='folio-ocr-') as directory:
        path = Path(directory) / 'page.png'
        image.convert('RGB').save(path)
        if engine == 'tesseract':
            command = ['tesseract', str(path), 'stdout', '-l', os.getenv('OCR_LANGUAGES', 'chi_sim+eng'), '--psm', '3']
        else:
            command = [str(vision_binary()), str(path)]
        try:
            result = subprocess.run(command, capture_output=True, timeout=120, check=True)
        except subprocess.TimeoutExpired as exc:
            raise ValueError('单页 OCR 超时，请降低图片分辨率后重试。') from exc
        except subprocess.CalledProcessError as exc:
            raise ValueError('本地 OCR 失败，请检查引擎与语言包是否完整安装。') from exc
        value = result.stdout.decode('utf-8', errors='replace').strip()
        if engine == 'apple-vision':
            value = '\n'.join(json.loads(value))
        return value


def vision_binary():
    from .config import DATA
    source=Path(__file__).with_name('vision_ocr.swift')
    cache=DATA/'ocr'
    cache.mkdir(exist_ok=True)
    target=cache/('vision-'+hashlib.sha256(source.read_bytes()).hexdigest()[:16])
    with _compile_lock:
        if not target.exists():
            with tempfile.TemporaryDirectory(prefix='compile-',dir=cache) as directory:
                binary=Path(directory)/'vision'
                try:
                    subprocess.run(['swiftc','-module-cache-path',str(Path(directory)/'modules'),str(source),'-o',str(binary)],capture_output=True,check=True,timeout=120)
                except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
                    raise ValueError('Apple Vision OCR 编译失败，请检查 Xcode 命令行工具，或安装 Tesseract。') from exc
                binary.replace(target)
    return target
