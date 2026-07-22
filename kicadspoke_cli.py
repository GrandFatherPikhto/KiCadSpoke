#!/usr/bin/env python3
"""
placer.py — главный скрипт для расстановки развязывающих конденсаторов.

Использование:
    python placer.py decap_placement.yaml [--dry-run] [--timeout-ms 20000] [--batch-size 10]
    python placer.py undo [--verbose]
"""

import argparse
import sys
import logging
import json
from typing import Dict, Any
import yaml
from pathlib import Path

# Добавляем корень проекта в sys.path
sys.path.insert(0, str(Path(__file__).parent))

from kicadspoke.config import load_config
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.placement.planner import PlacementPlanner
from kicadspoke.placement.executor import BatchExecutor   # <-- новый путь
from kicadspoke.exceptions import PlacerError
from kicadspoke.undo import undo_last_operation
from kicadspoke.validation import run_all_checks
from kicadspoke.registry import (PlacementRegistry, registry_path_for_config,
                                 TrackRegistry, track_registry_path_for_config)
from kicadspoke.template_extraction import extract_template_from_selection
from kicadspoke.constants import DEFAULT_TIMEOUT_MS, DEFAULT_BATCH_SIZE


def setup_logging(verbose: bool = False, log_file: str = None):
    """Настройка логирования: уровень и вывод в консоль и/или файл."""
    level = logging.DEBUG if verbose else logging.INFO
    handlers = []
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    handlers.append(console)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        handlers.append(file_handler)

    logging.basicConfig(level=logging.DEBUG, handlers=handlers)


def cmd_apply(args):
    """Основная команда: применить расстановку."""
    logger = logging.getLogger(__name__)
    logger.info(f"Загрузка конфига: {args.config}")
    cfg = load_config(args.config)

    all_clone_names = {c.name for c in cfg.clone_placements}
    if getattr(args, "clone_placement", None):
        name = args.clone_placement
        matching = [c for c in cfg.clone_placements if c.name == name]
        if not matching:
            all_names = [c.name for c in cfg.clone_placements]
            sys.exit(f"[ошибка] clone_placements с именем {name!r} не найден в конфиге. "
                    f"Доступные: {all_names}")
        cfg.clone_placements = matching
        logger.info(f"--clone-placement {name!r}: обрабатываю только его "
                   f"(остальные clone_placements в этом прогоне игнорируются)")

    logger.info(f"Подключение к KiCad (таймаут {args.timeout_ms} мс)")
    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    run_all_checks(adapter, cfg)

    logger.info("Планирование расстановки...")
    planner = PlacementPlanner(adapter, cfg)

    if args.dry_run:
        moves = planner.plan_moves()
        vias = planner.plan_vias()
        tracks = planner.plan_tracks()
        print("\n=== DRY RUN ===")
        print("Перемещения:")
        for m in moves:
            print(f"  {m.ref}: ({m.position.x/1e6:.3f}, {m.position.y/1e6:.3f}) мм, угол={m.angle.degrees:.1f}°")
        print("\nВиа:")
        for v in vias:
            print(f"  via у {v.owner_ref}: ({v.position.x/1e6:.3f}, {v.position.y/1e6:.3f}) мм, net={v.net_name}")
        print("\nТреки:")
        for t in tracks:
            print(f"  track у {t.owner_ref}: ({t.start.x/1e6:.3f}, {t.start.y/1e6:.3f}) -> "
                  f"({t.end.x/1e6:.3f}, {t.end.y/1e6:.3f}) мм, net={t.net_name}, "
                  f"width={t.width_mm} мм")
        print("\n(keepout термовиа посчитан по ТЕКУЩИМ позициям конденсаторов, "
              "не по целевым — может слегка отличаться от боевого прогона)")
        print("(коллизии треков с чужой медью/компонентами НЕ проверяются этим "
              "инструментом — полагаемся на DRC самого KiCad после расстановки)")
        return

    executor = BatchExecutor(adapter, cfg, batch_size=args.batch_size)
    registry = PlacementRegistry(adapter, registry_path_for_config(args.config))
    track_registry = TrackRegistry(adapter, track_registry_path_for_config(args.config))

    # --- Фаза 1: перемещения ---
    moves = planner.plan_moves()
    logger.info(f"Запланировано перемещений: {len(moves)}")
    logger.info("Применение перемещений...")
    failed_refs = executor.execute_moves(
        moves,
        check_collisions=not args.no_collision_check,
        collision_margin_mm=args.collision_margin,
    )
    if failed_refs:
        logger.warning(f"Не удалось переместить: {sorted(set(failed_refs))}")

    # --- Перечитываем плату ---
    logger.info("Обновление данных платы перед планированием виа...")
    adapter.refresh_board()

    # --- Фаза 2: виа ---
    all_vias = planner.plan_vias()
    vias_to_create = registry.reconcile(all_vias, known_clone_names=all_clone_names)
    logger.info(f"Запланировано виа: {len(all_vias)}, из них реально к созданию "
               f"(реестр отсеял уже стоящие правильно): {len(vias_to_create)}")
    logger.info("Применение виа...")
    failed_vias = executor.execute_vias(vias_to_create, registry=registry)
    if failed_vias:
        logger.warning(f"Не удалось создать виа рядом с: {sorted(set(failed_vias))}")

    # --- Фаза 3: треки ---
    all_tracks = planner.plan_tracks()
    tracks_to_create = track_registry.reconcile(all_tracks, known_clone_names=all_clone_names)
    logger.info(f"Запланировано треков: {len(all_tracks)}, из них реально к созданию "
               f"(реестр отсеял уже стоящие правильно): {len(tracks_to_create)}")
    logger.info("Применение треков...")
    failed_tracks = executor.execute_tracks(tracks_to_create, registry=track_registry)
    if failed_tracks:
        logger.warning(f"Не удалось создать треки рядом с: {sorted(set(failed_tracks))}")

    if not failed_refs and not failed_vias and not failed_tracks:
        logger.info("✅ Все операции выполнены успешно")
    else:
        logger.warning("⚠️ Некоторые операции завершились с ошибками – проверьте лог.")


def load_profile(profiles_path: str, top_key: str, profile_name: str) -> Dict[str, Any]:
    """
    Общий загрузчик именованных профилей CLI-аргументов (для extract и
    clone-extract — обе команды многословные, обе используют один и тот
    же механизм). top_key разный для разных команд (extract_profiles /
    clone_profiles) — так один YAML-файл может держать профили
    обеих команд сразу, без коллизии имён.
    """
    p = Path(profiles_path)
    if not p.exists():
        sys.exit(f"[ошибка] файл профилей {profiles_path!r} не найден")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    profiles = data.get(top_key, {})
    if profile_name not in profiles:
        available = list(profiles.keys())
        sys.exit(f"[ошибка] профиль {profile_name!r} не найден в {top_key!r} файла "
                 f"{profiles_path!r}. Доступные: {available}")
    return profiles[profile_name]


def cmd_extract(args):
    """Извлекает шаблон спицы из текущего выделения на плате в YAML."""
    logger = logging.getLogger(__name__)
    logger.info(f"Подключение к KiCad (таймаут {args.timeout_ms} мс)")
    adapter = KiCadBoardAdapter(timeout_ms=args.timeout_ms)
    adapter.refresh_board()

    direct_args_given = bool(args.name or args.output or args.param or args.net_template
                             or args.origin_by_via_net or args.origin_by_component_role)
    if args.profile and direct_args_given:
        sys.exit("[ошибка] --profile нельзя сочетать с --name/--output/--param/--net-template/"
                 "--origin-by-*: либо всё из профиля, либо всё явными флагами, не вперемешку")

    if args.profile:
        if not args.profiles:
            sys.exit("[ошибка] --profile указан без --profiles (файла профилей)")
        prof = load_profile(args.profiles, "extract_profiles", args.profile)
        for required in ("name", "output"):
            if required not in prof:
                sys.exit(f"[ошибка] в профиле {args.profile!r} нет обязательного поля {required!r}")
        name = prof["name"]
        output = prof["output"]
        params = dict(prof.get("param", {}) or {})
        net_template_map = dict(prof.get("net_template", {}) or {})
        origin_via_net = prof.get("origin_by_via_net")
        origin_component_role = prof.get("origin_by_component_role")
        logger.info(f"Профиль {args.profile!r} из {args.profiles}: name={name}, output={output}")
    else:
        name = args.name
        output = args.output
        if not name or not output:
            sys.exit("[ошибка] нужны --name и --output (или --profiles/--profile вместо них)")
        params = {}
        for item in (args.param or []):
            if "=" not in item:
                logger.error(f"--param {item!r} — нужен формат KEY=VALUE")
                sys.exit(1)
            k, v = item.split("=", 1)
            params[k] = v

        net_template_map = {}
        for item in (args.net_template or []):
            if "=" not in item:
                logger.error(f"--net-template {item!r} — нужен формат ЛИТЕРАЛ=ПАТТЕРН")
                sys.exit(1)
            literal, pattern = item.split("=", 1)
            net_template_map[literal] = pattern
        origin_via_net = args.origin_by_via_net
        origin_component_role = args.origin_by_component_role

    template_dict = extract_template_from_selection(
        adapter, name, params=params, net_template_map=net_template_map,
        origin_via_net=origin_via_net,
        origin_component_role=origin_component_role,
    )

    output_path = Path(output)
    is_json = output_path.suffix.lower() == '.json'
    existing = {}
    if output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            existing = (json.load(f) if is_json else yaml.safe_load(f)) or {}
        if name in existing:
            logger.warning(f"Шаблон {name!r} уже есть в {output_path} — будет перезаписан")

    existing.update(template_dict)

    with open(output_path, "w", encoding="utf-8") as f:
        if is_json:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        else:
            yaml.dump(existing, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    logger.info(f"✅ Шаблон {args.name!r} записан в {output_path}")


def cmd_undo(args):
    """Откатывает последнюю операцию."""
    logger = logging.getLogger(__name__)
    log_dir = Path("logs")
    if not log_dir.exists():
        logger.error("Папка logs не найдена.")
        return

    files = sorted(log_dir.glob("operation_*.json"), key=lambda p: p.stat().st_ctime)
    if not files:
        logger.error("Нет файлов операций для отката.")
        return

    last_file = files[-1]
    logger.info(f"Откат операции из {last_file.name}")
    success = undo_last_operation(last_file)
    if success:
        logger.info("✅ Операция успешно откатана.")
    else:
        logger.error("❌ Не удалось откатить операцию.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ['apply', 'undo', 'extract', 'clone-extract']:
        sys.argv.insert(1, 'apply')

    parser = argparse.ArgumentParser(
        description="KiCad Decap Placer – расстановка конденсаторов (ручная стратегия)",
        epilog="Пример: placer.py decap_placement.yaml --dry-run"
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Подкоманда")

    apply_parser = subparsers.add_parser("apply", help="Применить расстановку")
    apply_parser.add_argument("config", help="YAML конфигурационный файл")
    apply_parser.add_argument("--dry-run", action="store_true", help="Только распечатать план, не применять")
    apply_parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS, help="Таймаут IPC, мс")
    apply_parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Размер батча для коммитов")
    apply_parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    apply_parser.add_argument("--log-file", help="Файл для сохранения логов")
    apply_parser.add_argument("--no-collision-check", action="store_true", help="Отключить проверку коллизий")
    apply_parser.add_argument("--collision-margin", type=float, default=0.2, help="Дополнительный зазор при проверке коллизий, мм")
    apply_parser.add_argument("--clone-placement", metavar="NAME",
                              help="Обработать только ОДИН clone_placements с этим именем "
                                   "(остальные игнорируются на этот прогон) — нужно, если несколько "
                                   "clone_placements в режиме «по выделению»: в KiCad активно только "
                                   "одно выделение сразу, обработать все разом нельзя")

    undo_parser = subparsers.add_parser("undo", help="Откатить последнюю операцию")
    undo_parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    undo_parser.add_argument("--log-file", help="Файл для сохранения логов")

    clone_extract = subparsers.add_parser(
        "clone-extract",
        help="Снимок канала в YAML (файловый клонер, без IPC)")
    clone_extract.add_argument("--net", help="Путь к .net")
    clone_extract.add_argument("--pcb", help="Путь к .kicad_pcb")
    clone_extract.add_argument("--channel", help="Имя канала, напр. Channel_0")
    clone_extract.add_argument("--output", help="YAML-файл снимка")
    clone_extract.add_argument("--profiles", metavar="FILE",
                               help="YAML-файл именованных профилей clone-extract")
    clone_extract.add_argument("--profile", metavar="NAME",
                               help="Взять net/pcb/channel/output из профиля NAME в файле "
                                    "--profiles, вместо явных флагов (нельзя сочетать с ними)")
    clone_extract.add_argument("-v", "--verbose", action="store_true")

    extract_parser = subparsers.add_parser("extract", help="Извлечь шаблон спицы из текущего выделения")
    extract_parser.add_argument("--name", help="Имя шаблона (ключ в templates:)")
    extract_parser.add_argument("--output", help="Путь к YAML/JSON-файлу для записи")
    extract_parser.add_argument("--profiles", metavar="FILE",
                                help="YAML-файл именованных профилей extract (см. --profile)")
    extract_parser.add_argument("--profile", metavar="NAME",
                                help="Взять name/output/param/net-template/origin-by-* из "
                                     "профиля NAME в файле --profiles, вместо явных флагов "
                                     "(нельзя сочетать с --name и остальными явными флагами)")
    extract_parser.add_argument("--timeout-ms", type=int, default=20000, help="Таймаут IPC, мс")
    extract_parser.add_argument("--verbose", action="store_true", help="Подробный вывод")
    extract_parser.add_argument("--log-file", help="Файл для сохранения логов")
    extract_parser.add_argument("--param", action="append", metavar="KEY=VALUE",
                                help="Параметр для проверки --net-template (напр. channel=1); "
                                     "можно повторять; в шаблон НЕ пишется, нужен только "
                                     "для round-trip верификации паттернов")
    extract_parser.add_argument("--net-template", action="append", metavar="ЛИТЕРАЛ=ПАТТЕРН",
                                help="Явная карта реальная цепь -> паттерн с {placeholder} "
                                     "(напр. 'DAC1_DB1=DAC{channel}_DB1'); можно повторять; "
                                     "заполняет net_template ролей и параметризует via.net "
                                     "прямо при извлечении, вместо ручной правки YAML")
    origin_group = extract_parser.add_mutually_exclusive_group()
    origin_group.add_argument("--origin-by-via-net", metavar="NET",
                              help="Origin шаблона — позиция via на этой цепи (вместо bbox "
                                   "выделения); фатально, если такой цепи нет в выделении "
                                   "или она встречается больше одного раза")
    origin_group.add_argument("--origin-by-component-role", metavar="ROLE",
                              help="Origin шаблона — позиция компонента с этой ролью "
                                   "(вместо bbox выделения); фатально, если роли нет "
                                   "в выделении")

    args = parser.parse_args()

    setup_logging(verbose=getattr(args, "verbose", False), log_file=getattr(args, "log_file", None))

    try:
        if args.command == "apply":
            cmd_apply(args)
        elif args.command == "undo":
            cmd_undo(args)
        elif args.command == "clone-extract":
            direct_given = bool(args.net or args.pcb or args.channel or args.output)
            if args.profile and direct_given:
                sys.exit("[ошибка] --profile нельзя сочетать с --net/--pcb/--channel/--output")
            if args.profile:
                if not args.profiles:
                    sys.exit("[ошибка] --profile указан без --profiles (файла профилей)")
                prof = load_profile(args.profiles, "clone_profiles", args.profile)
                for required in ("net", "pcb", "channel", "output"):
                    if required not in prof:
                        sys.exit(f"[ошибка] в профиле {args.profile!r} нет обязательного поля {required!r}")
                net_path, pcb_path, channel, output = prof["net"], prof["pcb"], prof["channel"], prof["output"]
            else:
                if not (args.net and args.pcb and args.channel and args.output):
                    sys.exit("[ошибка] нужны --net/--pcb/--channel/--output (или --profiles/--profile)")
                net_path, pcb_path, channel, output = args.net, args.pcb, args.channel, args.output
            from kicadspoke.cloner.extract import extract_channel
            d = extract_channel(net_path, pcb_path, channel, output)
            s = d['summary']
            print(f"[{channel}] футпринтов: {s['footprints']}, "
                  f"сегментов: {s['segments']}, виа: {s['vias']} -> {output}")
        elif args.command == "extract":
            cmd_extract(args)
        else:
            parser.print_help()
            sys.exit(1)
    except PlacerError as e:
        logging.error(f"Ошибка: {e}")
        sys.exit(1)
    except Exception as e:
        logging.exception("Неожиданная ошибка")
        sys.exit(2)


if __name__ == "__main__":
    main()