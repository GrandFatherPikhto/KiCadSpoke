#!/usr/bin/env python3
"""
transform_template.py — преобразование шаблона спицы: поворот, зеркалирование,
перенос начала координат на заданный элемент (via или компонент).

Использование:
    python transform_template.py --input template.yaml --output new.yaml
        [--rotate 90] [--mirror-x] [--mirror-y]
        [--set-origin-by-via-index 0] [--set-origin-by-via-net "GND"]
        [--set-origin-by-component-role "LIGHT"] [--set-origin-by-component-index 1]
        [--origin-x 0.0 --origin-y 0.0]

Примеры:
    # Повернуть на 180° и сделать via с net "/Channel_0/DAC/+3V3_CLKVDD" началом координат
    python transform_template.py -i template.yaml -o new.yaml --rotate 180 --set-origin-by-via-net "/Channel_0/DAC/+3V3_CLKVDD"

    # Зеркалировать по X и перенести начало в компонент с ролью DAC_PI_FILTER_C1
    python transform_template.py -i template.yaml -o new.yaml --mirror-x --set-origin-by-component-role DAC_PI_FILTER_C1
"""

import argparse
import sys
import math
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

def load_template(input_path: str) -> Dict[str, Any]:
    """Загружает YAML-файл и возвращает словарь с одним шаблоном."""
    with open(input_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    if 'templates' in data:
        template_name = next(iter(data['templates']))
        template = data['templates'][template_name]
        return {'name': template_name, 'template': template}
    else:
        return {'name': 'template', 'template': data}

def save_template(output_path: str, template_name: str, template: Dict[str, Any]):
    """Сохраняет шаблон в YAML-файл с ключом templates."""
    data = {'templates': {template_name: template}}
    with open(output_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

def rotate_coords(along: float, across: float, angle_deg: float) -> Tuple[float, float]:
    """Поворачивает координаты против часовой стрелки на угол angle_deg (в градусах)."""
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    new_along = along * cos_a - across * sin_a
    new_across = along * sin_a + across * cos_a
    return new_along, new_across

def find_element(template: Dict[str, Any], origin_element: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Находит координаты элемента в исходном шаблоне (до преобразований)."""
    if origin_element.get('type') == 'via':
        idx = origin_element.get('index')
        if idx is not None:
            vias = template.get('vias', [])
            if 0 <= idx < len(vias):
                via = vias[idx]
                return via.get('offset_along_mm', 0.0), via.get('offset_across_mm', 0.0)
        net = origin_element.get('net')
        if net is not None:
            for via in template.get('vias', []):
                if via.get('net') == net:
                    return via.get('offset_along_mm', 0.0), via.get('offset_across_mm', 0.0)
    elif origin_element.get('type') == 'component':
        idx = origin_element.get('index')
        if idx is not None:
            comps = template.get('components', [])
            if 0 <= idx < len(comps):
                comp = comps[idx]
                return comp.get('offset_along_mm', 0.0), comp.get('offset_across_mm', 0.0)
        role = origin_element.get('role')
        if role is not None:
            for comp in template.get('components', []):
                if comp.get('role') == role:
                    return comp.get('offset_along_mm', 0.0), comp.get('offset_across_mm', 0.0)
    return None


def apply_transform(template: Dict[str, Any],
                    rotate_deg: float = 0.0,
                    mirror_x: bool = False,
                    mirror_y: bool = False,
                    origin_element: Optional[Dict[str, Any]] = None,
                    origin_x: Optional[float] = None,
                    origin_y: Optional[float] = None) -> Dict[str, Any]:
    """
    Применяет преобразования к шаблону.
    Порядок: сначала перенос начала координат (если задан), затем поворот и зеркалирование.
    """
    new_template = template.copy()
    
    # Определяем смещение (origin_along, origin_across) для переноса начала
    if origin_element is not None:
        coords = find_element(template, origin_element)
        if coords is None:
            raise ValueError(f"Не удалось найти элемент для переноса начала координат: {origin_element}")
        origin_along, origin_across = coords
    elif origin_x is not None and origin_y is not None:
        origin_along = origin_x
        origin_across = origin_y
    else:
        origin_along = 0.0
        origin_across = 0.0

    # Переносим начало координат (вычитаем смещение из всех элементов)
    # Сначала vias
    new_vias = []
    for via in template.get('vias', []):
        along = via.get('offset_along_mm', 0.0) - origin_along
        across = via.get('offset_across_mm', 0.0) - origin_across
        new_via = via.copy()
        new_via['offset_along_mm'] = round(along, 6)
        new_via['offset_across_mm'] = round(across, 6)
        new_vias.append(new_via)
    new_template['vias'] = new_vias

    # Переносим компоненты
    new_components = []
    for comp in template.get('components', []):
        along = comp.get('offset_along_mm', 0.0) - origin_along
        across = comp.get('offset_across_mm', 0.0) - origin_across
        angle = comp.get('angle_deg', 0.0)
        new_comp = comp.copy()
        new_comp['offset_along_mm'] = round(along, 6)
        new_comp['offset_across_mm'] = round(across, 6)
        new_comp['angle_deg'] = round(angle, 6)
        new_components.append(new_comp)
    new_template['components'] = new_components

    # Теперь применяем поворот и зеркалирование к уже смещённым координатам
    # Vias
    transformed_vias = []
    for via in new_template.get('vias', []):
        along = via.get('offset_along_mm', 0.0)
        across = via.get('offset_across_mm', 0.0)
        # Зеркалирование
        if mirror_x:
            across = -across
        if mirror_y:
            along = -along
        # Поворот
        if rotate_deg:
            along, across = rotate_coords(along, across, rotate_deg)
        new_via = via.copy()
        new_via['offset_along_mm'] = round(along, 6)
        new_via['offset_across_mm'] = round(across, 6)
        transformed_vias.append(new_via)
    new_template['vias'] = transformed_vias

    # Компоненты
    transformed_components = []
    for comp in new_template.get('components', []):
        along = comp.get('offset_along_mm', 0.0)
        across = comp.get('offset_across_mm', 0.0)
        angle = comp.get('angle_deg', 0.0)
        # Зеркалирование координат
        if mirror_x:
            across = -across
        if mirror_y:
            along = -along
        # Поворот координат
        if rotate_deg:
            along, across = rotate_coords(along, across, rotate_deg)
        # Коррекция угла: при зеркалировании угол меняет знак, при повороте добавляется rotation_deg
        if mirror_x or mirror_y:
            angle = -angle
        if rotate_deg:
            angle += rotate_deg
        # Нормализация угла в диапазон [0, 360)
        angle = angle % 360.0
        new_comp = comp.copy()
        new_comp['offset_along_mm'] = round(along, 6)
        new_comp['offset_across_mm'] = round(across, 6)
        new_comp['angle_deg'] = round(angle, 6)
        transformed_components.append(new_comp)
    new_template['components'] = transformed_components

    return new_template

def main():
    parser = argparse.ArgumentParser(description="Преобразование шаблона спицы")
    parser.add_argument('-i', '--input', required=True, help="Входной YAML-файл с шаблоном")
    parser.add_argument('-o', '--output', required=True, help="Выходной YAML-файл")
    parser.add_argument('--rotate', type=float, default=0.0, help="Поворот шаблона (градусы, против часовой стрелки)")
    parser.add_argument('--mirror-x', action='store_true', help="Зеркалирование по оси X (меняет знак across)")
    parser.add_argument('--mirror-y', action='store_true', help="Зеркалирование по оси Y (меняет знак along)")
    # Опции для переноса начала координат
    parser.add_argument('--set-origin-by-via-index', type=int, help="Индекс via (0-based), который станет началом координат")
    parser.add_argument('--set-origin-by-via-net', type=str, help="Net via, который станет началом координат")
    parser.add_argument('--set-origin-by-component-index', type=int, help="Индекс компонента (0-based), который станет началом координат")
    parser.add_argument('--set-origin-by-component-role', type=str, help="Роль компонента, который станет началом координат")
    parser.add_argument('--origin-x', type=float, help="Явное смещение по X (мм) для переноса начала")
    parser.add_argument('--origin-y', type=float, help="Явное смещение по Y (мм) для переноса начала")
    args = parser.parse_args()

    # Загружаем шаблон
    data = load_template(args.input)
    template_name = data['name']
    template = data['template']

    # Формируем спецификацию элемента для переноса начала
    origin_element = None
    if args.set_origin_by_via_index is not None:
        origin_element = {'type': 'via', 'index': args.set_origin_by_via_index}
    elif args.set_origin_by_via_net is not None:
        origin_element = {'type': 'via', 'net': args.set_origin_by_via_net}
    elif args.set_origin_by_component_index is not None:
        origin_element = {'type': 'component', 'index': args.set_origin_by_component_index}
    elif args.set_origin_by_component_role is not None:
        origin_element = {'type': 'component', 'role': args.set_origin_by_component_role}

    if args.origin_x is not None and args.origin_y is not None:
        origin_x = args.origin_x
        origin_y = args.origin_y
    else:
        origin_x = None
        origin_y = None

    # Применяем преобразования
    new_template = apply_transform(
        template,
        rotate_deg=args.rotate,
        mirror_x=args.mirror_x,
        mirror_y=args.mirror_y,
        origin_element=origin_element,
        origin_x=origin_x,
        origin_y=origin_y
    )

    # Сохраняем
    save_template(args.output, template_name, new_template)
    print(f"Шаблон преобразован и сохранён в {args.output}")

if __name__ == '__main__':
    main()