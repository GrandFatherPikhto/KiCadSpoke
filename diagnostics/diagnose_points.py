#!/usr/bin/env python3
import sys
import logging
import kipy

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("EnumInspector")

MM = 1000000.0  

def run_inspection():
    try:
        logger.debug("Подключение к сессии KiCad 10...")
        kicad_session = kipy.KiCad(timeout_ms=5000)
        board = kicad_session.get_board()
        
        if board is None:
            logger.error("Не удалось получить плату!")
            return

        # Пытаемся найти, где авторы kipy спрятали Enum типов объектов
        # Обычно это kipy.proto.common.types_pb2 или что-то похожее
        logger.info("Сканируем базу данных платы через подбор числовых Enum...")
        
        all_items = []
        # Перебираем числовые значения Enum, так как gRPC принимает их под капотом
        for type_idx in range(0, 30):
            try:
                # Передаем список из одного числового типа
                items = list(board.get_items(types=[type_idx]))
                if items:
                    logger.debug(f"➔ Числовой тип [{type_idx}] вернул объектов: {len(items)} (Пример класса: {type(items[0]).__name__})")
                    all_items.extend(items)
            except Exception as e:
                # Пропускаем неподдерживаемые или невалидные типы
                pass

        if not all_items:
            logger.warning("Не удалось извлечь объекты через числовой перебор get_items.")
            return

        logger.info(f"🎉 Всего из базы данных платы выгребли объектов: {len(all_items)}")

        # Собираем уникальные имена классов для анализа структуры kipy
        class_names = set(type(item).__name__ for item in all_items)
        logger.info(f"Доступные типы классов на плате:\n{sorted(list(class_names))}")

        # Фильтруем точки
        points = [item for item in all_items if "point" in type(item).__name__.lower()]

        if not points:
            logger.warning("❌ Точки привязки (Points) не обнаружены среди доступных типов.")
            print("\n💡 Инженерный вердикт для KiCadStamp:")
            print("   Инструмент 'Points' (add point) из KiCad 10 пока отсутствует в gRPC-протоколах kipy.")
            print("   Для автоматизации клонирования используйте железный и стабильный якорь —")
            print("   координаты центров или пинов микросхем ЦАП (IC601, IC1601, IC1101).\n")
            return

        print("\n" + "="*70)
        print(f"🎯 УСПЕХ! НАЙДЕНЫ ТОЧКИ ПРИВЯЗКИ ЧЕРЕЗ ALL_ITEMS: {len(points)}")
        print("="*70)
        for idx, pt in enumerate(points, 1):
            x_val = getattr(pt, 'position_x', getattr(pt, 'start_x', 0))
            y_val = getattr(pt, 'position_y', getattr(pt, 'start_y', 0))
            layer_name = getattr(pt, 'layer', 'Unknown')
            print(f"{idx:<3} | {type(pt).__name__:<22} | {layer_name:<12} | {x_val/MM:<12.4f} | {y_val/MM:<12.4f}")
        print("="*70)

    except Exception as e:
        logger.exception(f"Критическая ошибка: {e}")

if __name__ == "__main__":
    run_inspection()
