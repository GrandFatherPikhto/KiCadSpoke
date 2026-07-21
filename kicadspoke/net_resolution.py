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


def parametrize_net(literal_net: str, net_template_map: Dict[str, str],
                     params: Dict[str, Any]) -> str:
    """
    Обратная операция к resolve_net — для extract, не для apply.

    literal_net — реальное имя цепи, снятое с платы (v.net.name).
    net_template_map — явная, руками написанная карта литерал->паттерн
    (напр. {"DAC1_DB1": "DAC{channel}_DB1"}), задаётся один раз при
    extract через --net-template. params — те же params, которыми потом
    будет резолвиться этот же паттерн при apply (передаются при extract
    через --param только для проверки, в шаблон НЕ пишутся).

    НИКАКОГО угадывания позиции плейсхолдера по подстроке — паттерн
    полностью пишет человек. Единственное, что делает эта функция —
    проверяет, что написанный паттерн при резолве с данными params даёт
    обратно ровно тот литерал, с которого его сняли (round-trip), и
    фатально падает при малейшем расхождении (типичная причина —
    опечатка в паттерне или не тот параметр).
    """
    if literal_net not in net_template_map:
        return literal_net
    pattern = net_template_map[literal_net]
    check = resolve_net(pattern, params, {})
    if check != literal_net:
        raise ValidationError(format_fatal_error(
            f"--net-template для {literal_net!r} не проходит проверку",
            [f"паттерн {pattern!r} с params={params} резолвится в {check!r}, "
             f"а не в {literal_net!r} — опечатка в паттерне или не тот параметр "
             f"передан через --param"]
        ))
    return pattern