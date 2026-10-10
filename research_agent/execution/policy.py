"""Deterministic first-phase operation policy (never a shell command)."""
import hashlib
import json


def search_arguments(arguments):
    if not isinstance(arguments, dict) or set(arguments) - {'query', 'max_results'}:
        raise ValueError('只接受 query 和 max_results，不接受命令、路径或额外参数')
    query = arguments.get('query')
    limit = arguments.get('max_results', 20)
    if not isinstance(query, str) or not query.strip() or len(query) > 512 or '\x00' in query or '\n' in query or '\r' in query:
        raise ValueError('query 须为 1 到 512 字符的单行普通文本')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('max_results 须为 1 到 50 的整数')
    return {'query': query, 'max_results': limit}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()
