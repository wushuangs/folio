import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env.local')
_data = Path(os.getenv('DATA_DIR', str(ROOT / 'data')))
DATA = (_data if _data.is_absolute() else ROOT / _data).resolve()
DATA.mkdir(parents=True, exist_ok=True)
UPLOADS = DATA / 'uploads'
UPLOADS.mkdir(exist_ok=True)
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite:///{DATA / "knowledge.db"}')


def settings():
    zhipu_key = os.getenv('ZHIPU_API_KEY', '')
    llm_url = os.getenv('LLM_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4').rstrip('/')
    embedding_url = os.getenv('EMBEDDING_BASE_URL', 'https://open.bigmodel.cn/api/paas/v4').rstrip('/')
    from urllib.parse import urlparse
    return {
        'llm_url': llm_url,
        'llm_key': os.getenv('LLM_API_KEY', '') or (zhipu_key if urlparse(llm_url).hostname == 'open.bigmodel.cn' else ''),
        'llm_model': os.getenv('LLM_MODEL', 'glm-4.7'),
        'embedding_url': embedding_url,
        'embedding_key': os.getenv('EMBEDDING_API_KEY', '') or (zhipu_key if urlparse(embedding_url).hostname == 'open.bigmodel.cn' else ''),
        'embedding_model': os.getenv('EMBEDDING_MODEL', 'embedding-3'),
        'embedding_dimensions': int(os.getenv('EMBEDDING_DIMENSIONS', '0') or '0'),
    }


def embedding_signature():
    s = settings()
    base = f'{s["embedding_url"]}|{s["embedding_model"]}'
    return (base + (f'|dim={s["embedding_dimensions"]}' if s['embedding_dimensions'] else '')) if s['embedding_key'] and s['embedding_model'] else ''
