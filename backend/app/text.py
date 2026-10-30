import logging
import re
import unicodedata
from functools import lru_cache

import jieba

from .config import DATA

jieba.setLogLevel(logging.ERROR)
_segmenter = jieba.Tokenizer()
_segmenter.tmp_dir = str(DATA)
for term in ['加电','无反应','单台机组','单台','工作电压','起动时间','预热时间','通信地址']:
    _segmenter.add_word(term)
STOP = set('什么 怎么 如何 哪些 可以 是否 一个 这个 那个 请问 一下 时候 需要 手册 说明 根据 告诉 我们 进行 以及 中的 的是 有什 么是 怎样 多少 多少个 分别 应该 请 提供 介绍 下列 相关 的 了 是 在 和 与 及 吗 呢 时 后 不会 允许'.split())
GENERIC = set('控制器 设备 产品 模块 参数 设置 默认 默认值 范围 支持 功能 用途 接口 是什么 有哪些 使用 方法 要求 通过 采用 目前 能否 能不能 最新 项目 检查 排查 没有'.split())
ALIASES = {'通讯': '通信', '储存温度': '贮存温度', '存放温度': '贮存温度', '防水防尘': '防护等级', '防尘防水': '防护等级', '耗电': '功耗', '开不了机': '加电无反应', '上电没反应': '加电无反应', '连接不上': '通信失败', '连不上': '通信失败', '挖孔': '开孔尺寸', '安装孔': '开孔尺寸', '初始密码': '出厂密码', '加电后没有反应': '加电无反应', '加电后无反应': '加电无反应', '上电后没有反应': '加电无反应', '哪些项目': '项目'}


def normalize(text):
    text = unicodedata.normalize('NFKC', text).lower()
    text = re.sub(r'(?<![a-z0-9])rs[\s-]*(232|485)(?![0-9])',r'rs\1',text)
    if '温度' in text and re.search(r'仓库|存放|储存|存储',text) and '贮存温度' not in text:
        text = text.replace('温度','贮存温度')
    for old, new in ALIASES.items():
        text = text.replace(old, new)
    return text


@lru_cache(maxsize=8192)
def words(text):
    value = normalize(text)
    result = []
    for word in _segmenter.cut(value, HMM=False):
        word = word.strip()
        if re.fullmatch(r'[\w\u4e00-\u9fff]+', word) and word not in STOP and len(word) > 1:
            result.append(word)
    result.extend(re.findall(r'[a-z]+[a-z0-9]*(?:[_.-][a-z0-9]+)*|\d+(?:\.\d+)+', value))
    return tuple(dict.fromkeys(result))


@lru_cache(maxsize=8192)
def tokens(text):
    value = normalize(text)
    result = list(words(text))
    for part in re.findall(r'[\u4e00-\u9fff]+', value):
        result.extend(part[i:i+2] for i in range(len(part)-1) if part[i:i+2] not in STOP)
    return tuple(result)


def focus_words(question):
    result = [w for w in words(question) if w not in GENERIC and not re.fullmatch(r'[a-z]+\d[\w.-]*',w)]
    return result or [w for w in words(question) if not re.fullmatch(r'[a-z]+\d[\w.-]*',w)]


def content_terms(text):
    return ' '.join(tokens(text))
