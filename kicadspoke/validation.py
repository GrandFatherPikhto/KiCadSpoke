# kicadspoke/validation.py
"""
validation.py — фатальные предварительные проверки, выполняются ДО
планирования и любых изменений на плате. При обнаружении проблемы —
ValidationError с понятным, собранным сразу по всем найденным проблемам
сообщением (не одна ошибка за прогон, а полный список).

ИЗМЕНЕНО (KiCadSpoke 4.0): раньше проверялись явные ref в конфиге
(component1_ref/component2_ref) — их больше нет, компоненты подбираются
из ComponentPool по (реальная цепь, роль). Основная защита теперь встроена
в сам ComponentPool.pop() (фатально при нехватке), но здесь — тот же самый
подсчёт делается ЗАРАНЕЕ, чтобы увидеть все нехватки сразу, а не
останавливаться на первой попавшейся спице.
"""
import logging
import difflib
from typing import List, Dict
from .config import Config
from .kicad.adapter import KiCadBoardAdapter
from .exceptions import ValidationError, format_fatal_error
from .net_resolution import resolve_net
from .placement.services.component_pool import ComponentPool
from .placement.services.clone_role_resolver import clone_uses_selection_mode

logger = logging.getLogger(__name__)


def check_templates_and_pads_exist(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Каждая спица должна ссылаться на существующий шаблон и существующую
    площадку целевого компонента — иначе спица просто тихо пропускается
    (было бы легко не заметить опечатку в имени шаблона/номере пада).
    """
    problems = []
    anchors = {}
    for rule in cfg.rules:
        if rule.anchor_ref not in anchors:
            anchors[rule.anchor_ref] = adapter.get_footprint(rule.anchor_ref)
            if anchors[rule.anchor_ref] is None:
                problems.append(f"правило (цепь {rule.net!r}): якорь {rule.anchor_ref!r} "
                                f"не найден на плате")

    for rule in cfg.rules:
        target_fp = anchors.get(rule.anchor_ref)
        for spoke in rule.spokes:
            if not spoke.enabled:
                continue
            if spoke.template not in cfg.templates:
                problems.append(
                    f"спица (пад {spoke.pad}, цепь {rule.net!r}): "
                    f"шаблон {spoke.template!r} не найден в templates"
                )
                continue
            pad = adapter.get_pad_by_number(target_fp, spoke.pad) if target_fp else None
            if target_fp is not None and pad is None:
                problems.append(
                    f"спица (шаблон {spoke.template!r}, цепь {rule.net!r}): "
                    f"у {rule.anchor_ref} нет площадки {spoke.pad!r}"
                )

    if problems:
        raise ValidationError(format_fatal_error("спица ссылается на несуществующий шаблон или площадку", problems))
    logger.debug("Проверка шаблонов/падов спиц: все ссылки корректны")


def check_role_pool_sufficiency(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Для каждой цепи правила заранее считает, сколько компонентов каждой
    роли требуется всеми её спицами, и сверяет с реальным количеством
    компонентов на плате (та же цепь + поле Role) — фатально и со списком
    всех нехваток разом, если не сходится хоть где-то.
    """
    problems = []

    for rule in cfg.rules:
        needed_counts: Dict[str, int] = {}
        for spoke in rule.spokes:
            if not spoke.enabled:
                continue
            template = cfg.templates.get(spoke.template)
            if template is None:
                continue  # уже поймано check_templates_and_pads_exist
            for slot in template.components:
                needed_counts[slot.role] = needed_counts.get(slot.role, 0) + 1

        if not needed_counts:
            continue

        pool = ComponentPool(adapter, rule.net, roles=sorted(needed_counts.keys()))
        for role, needed in needed_counts.items():
            available = pool.remaining_count(role)
            if available < needed:
                problems.append(
                    f"цепь {rule.net!r}, роль {role!r}: нужно {needed}, найдено {available} "
                    f"(проверьте поле Role в схеме и реальное подключение к цепи)"
                )

    if problems:
        raise ValidationError(format_fatal_error("не хватает компонентов для ролей шаблона", problems))
    logger.debug("Проверка достаточности пулов по ролям: всё сходится")


def check_clone_templates_exist(cfg: Config) -> None:
    """
    Каждый ClonePlacement должен ссылаться на существующий шаблон — чисто
    конфиговая проверка, живой платы не требует вообще.
    """
    problems = []
    for clone in cfg.clone_placements:
        if not clone.enabled:
            continue
        if clone.template not in cfg.templates:
            problems.append(f"clone_placements {clone.name!r}: шаблон {clone.template!r} не найден в templates")
    if problems:
        raise ValidationError(format_fatal_error("clone_placements ссылается на несуществующий шаблон", problems))
    logger.debug("Проверка шаблонов clone_placements: все ссылки корректны")


def check_no_duplicate_clone_anchors(cfg: Config) -> None:
    """
    Чисто конфиговая проверка (живой платы не требует):
      1. Имена clone_placements[].name должны быть уникальны — это теперь
         единственный идентификатор для anchor-less размещений (см.
         clone_anchor_id) и в любом случае годная гигиена конфига.
      2. (template, anchor_ref, anchor_pad) среди clone_placements С
         заданным anchor_ref должен быть уникален — это ровно та
         identity, по которой теперь живёт реестр (registry.py); если
         два разных clone_placement случайно указывают один и тот же
         физический якорь под одним шаблоном, реестр не сможет их
         различить и будет путать via/треки одного с другим. Совпадение
         почти наверняка copy-paste опечатка (забыли поменять anchor_pad
         во втором блоке), а не осознанное намерение.
    """
    problems = []
    seen_names = {}
    seen_ref_anchors = {}
    seen_role_anchors = {}
    for clone in cfg.clone_placements:
        if not clone.enabled:
            continue
        if clone.name in seen_names:
            problems.append(f"имя {clone.name!r} встречается дважды в clone_placements — "
                            f"имена должны быть уникальны")
        seen_names[clone.name] = True

        if clone.anchor_ref is not None:
            key = (clone.template, clone.anchor_ref, clone.anchor_pad)
            if key in seen_ref_anchors:
                problems.append(f"{clone.name!r} и {seen_ref_anchors[key]!r}: оба указывают один и тот же "
                                f"якорь (template={clone.template!r}, anchor_ref={clone.anchor_ref!r}, "
                                f"anchor_pad={clone.anchor_pad!r}) — реестр не сможет различить их via/"
                                f"треки; похоже на copy-paste опечатку (забыли поменять anchor_pad)")
            seen_ref_anchors[key] = clone.name

        if clone.anchor_role is not None:
            key = (clone.template, clone.anchor_role, clone.anchor_sheet, clone.anchor_pad)
            if key in seen_role_anchors:
                problems.append(f"{clone.name!r} и {seen_role_anchors[key]!r}: оба указывают один и тот же "
                                f"якорь (template={clone.template!r}, anchor_role={clone.anchor_role!r}, "
                                f"anchor_sheet={clone.anchor_sheet!r}, anchor_pad={clone.anchor_pad!r}) — "
                                f"реестр не сможет различить их via/треки; похоже на copy-paste опечатку "
                                f"(забыли поменять anchor_sheet/anchor_pad)")
            seen_role_anchors[key] = clone.name

    if problems:
        raise ValidationError(format_fatal_error("clone_placements с неоднозначной identity", problems))
    logger.debug("Проверка на дубликаты имён/якорей clone_placements: всё сходится")


def check_anchor_sheet_configured(cfg: Config) -> None:
    """
    Чисто конфиговая проверка. anchor_sheet резолвится через Config.
    sheet_names (см. sheet_names.py) — если он пуст, значит ни
    schematic_dir, ни schematic_files не заданы (или заданы, но ни один
    .kicad_sch не распарсился), и anchor_sheet НИКОГДА ничего не сузит —
    молча пройдёт мимо, как будто его не было, и neoднозначность
    anchor_role потом упадёт с менее полезным фаталом. Лучше сказать
    прямо и сразу, в чём дело.
    """
    users = [c.name for c in cfg.clone_placements if c.enabled and c.anchor_sheet]
    if users and not cfg.sheet_names:
        raise ValidationError(format_fatal_error(
            "anchor_sheet используется, но словарь листов пуст",
            [f"clone_placements с anchor_sheet: {users}",
             "нужен schematic_dir (или schematic_files) в корне конфига — "
             "путь к папке с *.kicad_sch, относительно самого этого YAML"]
        ))
    logger.debug("Проверка anchor_sheet/sheet_names: всё сходится")


def check_clone_nets_exist_on_board(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """
    Резолвит via.net КАЖДОГО clone_placement (и уровня спицы, и вложенных
    в components[i].vias — см. apply_clone_geometry) и сверяет результат
    со словарём реальных цепей платы (adapter.get_all_nets()).

    Зачем отдельно от resolve_roles_by_nets: сопоставление роль->ref уже
    само себя проверяет (кандидат ищется среди реальных падов, несуществующая
    цепь просто не найдёт кандидатов — фатал есть). А вот via.net идёт в
    ViaCommand НАПРЯМУЮ, без такой проверки — опечатка в net_overrides
    или в params, которая всё равно даёт синтаксически валидную строку
    (например "+3V3_DVD" вместо "+3V3_DVDD"), тихо создаст via на новой,
    не той цепи, никакой фатал по пути не сработает. Эта проверка — и
    есть тот самый недостающий словарь.

    via.net=None не проверяется здесь — это уже фатал в clone_geometry.py
    (у ClonePlacement нет дефолтной цепи), дублировать незачем.
    """
    problems = []
    real_nets = {n.name for n in adapter.get_all_nets()}

    def _check_via(via, clone, where: str):
        if via.net is None:
            return
        try:
            resolved = resolve_net(via.net, clone.params, clone.net_overrides)
        except ValidationError:
            return  # недостающий параметр — уже своя фатальная ошибка выше по стеку
        if resolved not in real_nets:
            hint = difflib.get_close_matches(resolved, real_nets, n=1)
            suggestion = f" — похоже на {hint[0]!r}?" if hint else ""
            problems.append(f"{clone.name!r}, {where}: via.net {via.net!r} резолвится в "
                            f"{resolved!r}, а такой цепи на плате нет{suggestion}")

    for clone in cfg.clone_placements:
        if not clone.enabled:
            continue
        template = cfg.templates.get(clone.template)
        if template is None:
            continue  # уже поймано check_clone_templates_exist
        for via in template.vias:
            _check_via(via, clone, "via уровня спицы")
        for slot in template.components:
            for via in slot.vias:
                _check_via(via, clone, f"via роли {slot.role!r}")

    if problems:
        raise ValidationError(format_fatal_error(
            "резолвнутая цепь via ссылается на несуществующую цепь платы", problems
        ))
    logger.debug("Проверка via.net clone_placements против реальных цепей платы: всё сходится")


def check_single_selection_based_clone(cfg: Config) -> None:
    """
    В KiCad в любой момент активно ТОЛЬКО ОДНО выделение — значит, за один
    прогон нельзя обработать больше одного ClonePlacement в режиме "по
    выделению" (нет ни nets, ни params). Если их больше одного — фатально,
    с подсказкой либо выключить лишние (enabled: false), либо запускать
    apply отдельно на каждый через --clone-placement NAME.
    """
    selection_based = [c.name for c in cfg.clone_placements if c.enabled and clone_uses_selection_mode(c)]
    if len(selection_based) > 1:
        raise ValidationError(format_fatal_error(
            "несколько clone_placements в режиме «по выделению» в одном прогоне",
            [f"найдено {len(selection_based)}: {selection_based} — в KiCad активно только одно "
             f"выделение сразу, обработать все сразу нельзя",
             "решение: либо enabled: false у всех, кроме одного, либо запускать "
             "apply отдельно для каждого через --clone-placement NAME"]
        ))
    logger.debug("Проверка на множественное выделение в clone_placements: сходится")


def run_all_checks(adapter: KiCadBoardAdapter, cfg: Config) -> None:
    """Запускает все проверки по порядку — от дешёвых к более полным."""
    logger.info("Предварительные проверки конфигурации...")
    check_clone_templates_exist(cfg)
    check_no_duplicate_clone_anchors(cfg)
    check_anchor_sheet_configured(cfg)
    check_single_selection_based_clone(cfg)
    check_templates_and_pads_exist(adapter, cfg)
    check_role_pool_sufficiency(adapter, cfg)
    check_clone_nets_exist_on_board(adapter, cfg)
    logger.info("Все предварительные проверки пройдены")