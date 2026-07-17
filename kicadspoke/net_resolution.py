# kicadspoke/net_resolution.py
"""
net_resolution.py — трёхслойное разрешение имени цепи для клонируемых
шаблонов (TemplatePlacer), по возрастанию частности:

  1. Литерал ("GND") — как есть, если нет плейсхолдеров.
  2. Плейсхолдер ("DAC{channel}_DB1") — подставляется из params
     (str.format), один раз вписанный вручную в шаблон при извлечении/
     редактировании — НЕ выводится автоматически по какому-либо
     паттерну.
  3. net_overrides — применяется ПОВЕРХ результата пп. 1-2, по итоговому
     (уже подставленному) имени — для точечных исключений вроде
     иерархических путей (/STM32F4xx/BOOT0), которые не укладываются
     даже в параметризацию.

Никакого автоматического угадывания нигде — оба механизма (params и
net_overrides) требуют явной, руками вписанной настройки.
"""
from typing import Dict, Any
from .exceptions import ValidationError, format_fatal_error


def resolve_net(net_template: str, params: Dict[str, Any], net_overrides: Dict[str, str]) -> str:
    """
    net_template — имя цепи как записано в шаблоне (TemplateVia.net),
    возможно с {placeholder}. params — значения для подстановки (из
    ClonePlacement.params). net_overrides — точечная подмена итогового
    имени (из ClonePlacement.net_overrides).
    """
    try:
        resolved = net_template.format(**params)
    except KeyError as e:
        raise ValidationError(format_fatal_error(
            f"в цепи {net_template!r} есть плейсхолдер, для которого не задан параметр",
            [f"не хватает параметра {e} — добавьте его в params этого clone_placements, "
             f"или уберите плейсхолдер из шаблона"]
        ))
    return net_overrides.get(resolved, resolved)
