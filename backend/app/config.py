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


def model_profiles():
    import json
    import re
    from urllib.parse import urlparse
    default = settings()
    profiles = [{'id': 'default', 'label': default['llm_model'] or '默认模型', 'model': default['llm_model'],
                 'base_url': default['llm_url'], 'api_key': default['llm_key'], 'local': urlparse(default['llm_url']).hostname in {'localhost','127.0.0.1','::1'}}]
    try:
        extra = json.loads(os.getenv('LLM_PROFILES', '[]'))
        if not isinstance(extra,list) or len(extra)>12:
            raise ValueError()
        for entry in extra:
            identifier=entry['id']
            if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',identifier) or identifier in {p['id'] for p in profiles}:
                raise ValueError()
            url=entry['base_url'].rstrip('/')
            parsed=urlparse(url)
            if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError()
            key_env=entry.get('api_key_env','')
            if key_env and not re.fullmatch(r'[A-Z][A-Z0-9_]*',key_env):raise ValueError()
            model=entry['model']
            label=entry.get('label',model)
            if not isinstance(model,str) or not model or len(model)>200 or not isinstance(label,str) or len(label)>100:raise ValueError()
            profiles.append({'id':identifier,'label':label,'model':model,'base_url':url,'api_key':os.getenv(key_env,'') if key_env else '', 'local':parsed.hostname in {'localhost','127.0.0.1','::1'}})
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        raise ValueError('LLM_PROFILES 配置无效，请检查模型列表、唯一 ID、接口地址和密钥环境变量名。') from exc
    return profiles


def resolve_model(identifier=None):
    profile=next((p for p in model_profiles() if p['id']==(identifier or 'default')),None)
    if not profile:raise ValueError('所选模型不存在，请刷新模型列表。')
    return profile


def public_models():
    return [{'id':p['id'],'label':p['label'],'model':p['model'],'ready':bool(p['api_key'] or p.get('local'))} for p in model_profiles()]
