# kicadspoke/cloner/sexp.py
"""
Тонкие помощники над sexpdata. Выбор sexpdata вместо schema-aware
библиотек — осознанный: формат KiCad синтаксически стабилен с v6,
словарь только растёт, и generic-парсер переваривает новые токены
прозрачно (проверено: kinparse спотыкается о нетлист KiCad 10,
sexpdata — нет).
"""

import sexpdata


def sval(x):
    """Symbol -> str, остальное как есть."""
    return x.value() if isinstance(x, sexpdata.Symbol) else x


def is_node(n, key: str) -> bool:
    return isinstance(n, list) and bool(n) and sval(n[0]) == key


def children(node, key: str):
    """Все дочерние узлы (key ...)."""
    return [n for n in node if is_node(n, key)]


def child(node, key: str, default=None):
    """Первый дочерний узел (key ...) или default."""
    for n in node:
        if is_node(n, key):
            return n
    return default


def atom(node, key: str, default=None):
    """Значение первого атома узла (key value): child(node,key)[1]."""
    c = child(node, key)
    if c is None or len(c) < 2:
        return default
    return sval(c[1])


def load_file(path: str):
    with open(path, encoding="utf-8", errors="replace") as f:
        return sexpdata.loads(f.read())
