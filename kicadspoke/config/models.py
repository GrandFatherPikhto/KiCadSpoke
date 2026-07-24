# kicadspoke/config/models.py
"""
config/models.py — все dataclass'ы конфигурации (шаблоны, ClonePlacement,
Rule, Config и т.д.), БЕЗ единой строчки логики загрузки/валидации YAML —
это чисто описание формы данных. Загрузка — в config/loader.py.

Разделено рефакторингом из монолитного config.py (652 строки), который
распухал каждый раз, когда добавлялось новое поле — dataclass и его
загрузчик правились в одном и том же файле вперемешку с остальными 8
dataclass'ами и их загрузчиками. Публичный интерфейс пакета не изменился —
kicadspoke/config/__init__.py реэкспортирует всё отсюда и из loader.py,
так что `from kicadspoke.config import Config, ClonePlacement, load_config`
продолжает работать один в один, как раньше.
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class ThermalViaArrayConfig:
    """Конфигурация массива тепловых via под термопадом IC."""
    enabled: bool = False
    anchor_ref: str = ""
    pad: str = ""
    net: str = "GND"
    rows: int = 4
    cols: int = 4
    margin_mm: float = 0.5
    pattern: str = "grid"
    drill_mm: float = 0.3
    diameter_mm: float = 0.5


@dataclass
class TemplateVia:
    """
    Via-слот шаблона — координаты ВСЕГДА along/across от нуля СПИЦЫ (не от
    пада компонента, даже если слот принадлежит конкретной роли компонента!)
    — та же самая формула (local_to_absolute), что и для позиции самого
    компонента. net=None означает "взять цепь правила" (rule.net).

    ИЗМЕНЕНО (KiCadSpoke): раньше power_via был единственным полем на
    уровне спицы, а GND via компонента считалась от РЕАЛЬНОГО пада уже
    размещённого компонента (требовало чтения живой платы после коммита).
    Теперь обе идеи — один и тот же слот, чистая геометрия шаблона, без
    зависимости от живой платы вообще. Списков может быть сколько угодно,
    на обоих уровнях (spoke.vias и component.vias).
    """
    offset_along_mm: float = 0.0
    offset_across_mm: float = 0.0
    net: Optional[str] = None
    drill_mm: float = 0.3
    diameter_mm: float = 0.6


@dataclass
class TemplateComponentSlot:
    """
    Один компонент-слот в шаблоне — роль ('HEAVY'/'LIGHT'/'XTAL'/
    'LOAD_CAP_1' и т.д.), а не конкретный ref. Роли ОБЯЗАНЫ быть уникальны
    внутри одного шаблона (проверяется фатально при загрузке, см.
    _load_spoke_template). Конкретный ref подбирается на этапе расстановки
    из пула компонентов платы: все футпринты, чей РЕАЛЬНЫЙ пад сидит на
    цепи правила (rule.net) и у кого пользовательское поле Role совпадает
    с этой ролью (см. placement/services/component_pool.py).
    Координаты локальные (along/across) — от нуля СПИЦЫ, не от самого
    компонента. via этого слота — та же локальная система, см. TemplateVia.

    net_template — ОПЦИОНАЛЬНО, для TemplatePlacer (сопоставление роли
    по цепям, а не по выделению): ожидаемая цепь этого компонента, тем же
    синтаксисом плейсхолдеров, что и TemplateVia.net (см.
    net_resolution.py). Для ManualSpoke/component_pool.py не используется
    вообще — там роль ищется по (rule.net, Role), без своего поля здесь.
    """
    role: str
    offset_along_mm: float = 0.0
    offset_across_mm: float = 0.0
    angle_deg: float = 0.0
    vias: List[TemplateVia] = field(default_factory=list)
    net_template: Optional[str] = None
    # Слой слота — ФАКТ, абсолютный: 'F.Cu' | 'B.Cu'. None = наследовать
    # layer шаблона. Пишется extract'ом только для компонентов,
    # выбивающихся из слоя шаблона.
    layer: Optional[str] = None


@dataclass
class TemplateTrack:
    """
    Прямой отрезок медной дорожки в шаблоне — та же локальная система
    координат (along/across от нуля СПИЦЫ), что и у TemplateVia. Никакой
    привязки к ролям/падам: как и via, дорожка не имеет пользовательских
    полей, "чья" она — определить нельзя, доверяем геометрии (все
    элементы шаблона двигаются/крутятся/зеркалятся одной и той же
    формулой, см. geometry/clone_geometry.py).

    Ломаная дорожка — это просто НЕСКОЛЬКО TemplateTrack подряд,
    стыкующихся концами (ровно так kipy.board_types.Track хранит их и
    внутри самого KiCad — нет отдельной сущности "полилиния"). Дуги
    (ArcTrack) сознательно не поддержаны — не нужны для П-фильтров,
    отдельное расширение при необходимости.

    Коллизии (не пересекает ли дорожка чужую медь/компонент на новом
    месте) НЕ проверяются этим инструментом вообще — сознательное
    решение (см. обсуждение в чате): полагаемся на DRC самого KiCad
    после расстановки, а не строим свою geometry-проверку отрезок-против-
    отрезка.
    """
    start_along_mm: float = 0.0
    start_across_mm: float = 0.0
    end_along_mm: float = 0.0
    end_across_mm: float = 0.0
    width_mm: float = 0.25
    net: Optional[str] = None
    # Слой — тот же паттерн, что у TemplateComponentSlot.layer: None =
    # наследовать layer шаблона, при mirror инвертируется той же строкой.
    layer: Optional[str] = None


@dataclass
class SpokeTemplate:
    """
    Шаблон спицы — вся геометрия локальная и поворотоинвариантная:
    описывается один раз при rotation_deg=0 (условный эталонный борт),
    дальше конкретная спица поворачивает его целиком на свой угол.
    Любой из элементов может отсутствовать/быть пустым — например, спица
    без единой via, или шаблон всего с одним компонентом.
    """
    name: str
    vias: List[TemplateVia] = field(default_factory=list)
    components: List[TemplateComponentSlot] = field(default_factory=list)
    tracks: List[TemplateTrack] = field(default_factory=list)
    # Слой шаблона — ФАКТ, абсолютный: 'F.Cu' | 'B.Cu', как снято
    # экстракцией (пишется автоматически). Компоненты без своего layer
    # наследуют его. Никакой автоматики сторон: шаблон кладётся буква в
    # букву; перевернуть целиком — явный mirror у размещения.
    layer: str = 'F.Cu'


@dataclass
class ManualSpoke:
    """
    Конкретная спица на конкретном паде FPGA. shift_x_mm/shift_y_mm и
    rotation_deg — ВСЕГДА в обычных координатах KiCad (не локальных),
    подбираются глазами под конкретный борт. Порядок применения: сначала
    сдвиг (shift_x, shift_y) от центра пада к нулю спицы, затем поворот
    получившегося нуля (и всего содержимого шаблона) на rotation_deg.

    ВАЖНО: никаких ref компонентов здесь больше нет — конкретные
    компоненты подбираются автоматически из пула (см.
    placement/services/component_pool.py) по совпадению реальной цепи
    (rule.net) и пользовательского поля Role на компоненте, в порядке
    следования спиц в этом списке.
    """
    pad: str
    template: str
    shift_x_mm: float = 0.0
    shift_y_mm: float = 0.0
    rotation_deg: float = 0.0
    enabled: bool = True


@dataclass
class Rule:
    """Правило: выводок спиц вокруг ОДНОГО якорного компонента.
    anchor_ref ИЛИ anchor_role (взаимоисключающе, ровно одно обязательно) —
    чей это выводок (пады спиц — его пады). anchor_sheet/anchor_cluster —
    сужение неоднозначности anchor_role, тот же принцип, что у
    ClonePlacement (см. config/models.py)."""
    net: str
    spokes: List[ManualSpoke]
    anchor_ref: Optional[str] = None
    anchor_role: Optional[str] = None
    anchor_sheet: Optional[str] = None
    anchor_cluster: Optional[str] = None


@dataclass
class ClonePlacement:
    """
    Применение шаблона на новом месте (TemplatePlacer/Cloner) — в отличие
    от ManualSpoke (якорь = номер пада IC), якорь здесь — просто имя,
    ни с каким конкретным компонентом не связанное (anchor_id в реестре
    = f"name:{name}"). Два режима позиционирования:
      - anchor_ref задан: ноль = центр пада anchor_pad (или центр
        футпринта, если anchor_pad нет), origin_x_mm/origin_y_mm —
        необязательный ПЛОСКИЙ сдвиг от якоря (без поворота, как shift
        у ManualSpoke), rotation_deg крутит только содержимое шаблона.
      - anchor_ref не задан: origin_x_mm/origin_y_mm — АБСОЛЮТНАЯ
        точка на плате (обязательны), как раньше.

    Сопоставление роль->ref — ЛИБО через текущее выделение на плате
    (для редких, штучных секций типа одной MCU), ЛИБО через явные цепи
    (params/nets/net_overrides — для многократно повторяющихся секций
    вроде П-фильтров или каналов ЦАП). Наличие params ИЛИ nets означает
    режим "по цепям"; их отсутствие — режим "по выделению".

    template ИЛИ role (взаимоисключающе, ровно одно обязательно):
      - template: как раньше, ссылка на SpokeTemplate из cfg.templates.
      - role: для ОДНОКОМПОНЕНТНОГO размещения без единой via/трека —
        заводить отдельный файл шаблона ради одной роли неудобно (см.
        обсуждение в чате). ClonePositionCalculator синтезирует
        одноразовый SpokeTemplate "на лету" (один компонент этой роли,
        offset (0,0), угол 0) — templates: в YAML вообще не трогается.
    """
    name: str
    origin_x_mm: float
    origin_y_mm: float
    rotation_deg: float = 0.0
    template: Optional[str] = None
    role: Optional[str] = None
    nets: Dict[str, str] = field(default_factory=dict)      # role -> net (буквально)
    params: Dict[str, Any] = field(default_factory=dict)     # для {placeholder} в net шаблона
    net_overrides: Dict[str, str] = field(default_factory=dict)  # финальная подмена resolved-имени
    enabled: bool = True
    anchor_ref: Optional[str] = None
    anchor_pad: Optional[str] = None
    # Альтернатива anchor_ref — якорь по полю Role на плате, а не по
    # refdes (переживает реаннотацию/перенумерацию — refdes для этого не
    # надёжен, см. обсуждение). Взаимоисключающе с anchor_ref (фатал при
    # обоих сразу — см. _check_anchor_fields в load_config). anchor_sheet —
    # ТОЛЬКО сужение неоднозначности при 2+ кандидатах с одним anchor_role
    # (сравнение по префиксу имени ЛОКАЛЬНОЙ иерархической цепи вида
    # '/Channel_0/...' — НЕ через sheet_path/UUID, это эмпирически не
    # сработало, см. пробные скрипты в чате). Без смысла без anchor_role.
    anchor_role: Optional[str] = None
    anchor_sheet: Optional[str] = None
    # Cluster — второе кастомное поле схемы (см. constants.CLUSTER_FIELD_NAME),
    # физический экземпляр/кластер, независимо от anchor_ref/anchor_role.
    # Используется в ДВУХ местах: (1) сужение поиска anchor_role (как
    # anchor_sheet, только по другому полю), (2) сужение неоднозначных
    # ролей ВНУТРИ шаблона в resolve_roles_by_nets (замена мёртвой
    # _sheet_key-ступени — типовой случай "4 одинаковых C_IN_BULK на одном
    # листе, а листа-разделителя у них нет, потому что общая силовая
    # цепь"). Сравнение — по сегментам ПРЕФИКСА ('Channel_1' матчит и
    # 'Channel_1', и 'Channel_1/1V2_PLL_PI_FILTER'), не по точному
    # равенству — иерархия и плоские имена работают одним и тем же кодом.
    anchor_cluster: Optional[str] = None
    # Слой размещения — ФАКТ: None = слой шаблона (кладём буква в букву).
    # mirror — ОПЕРАЦИЯ, всегда ручная: перевернуть конструкцию целиком
    # (геометрия в зеркало, углы 180°−φ, все слои инвертируются).
    # Противоречие двух твоих же слов — фатал при загрузке: mirror без
    # смены слоя или смена слоя без mirror не имеют физического смысла.
    layer: Optional[str] = None
    mirror: bool = False
    # Явный override роль -> ref (высший приоритет, минуя поиск по цепям):
    # последнее средство, когда кандидаты электрически неразличимы
    # (три одинаковых фильтра в одном листе).
    refs: Dict[str, str] = field(default_factory=dict)
    # Явный запрос режима "по выделению" — НЕ выводится из отсутствия
    # nets/params (это старое, implicit-поведение остаётся дефолтом для
    # обратной совместимости, см. clone_uses_selection_mode). Нужен
    # отдельно от implicit-режима потому, что params используется ТАКЖЕ
    # для резолва плейсхолдеров via/track (apply_clone_geometry вызывает
    # resolve_net независимо от режима ролей) — без этого флага заданный
    # params для одной лишь via молча и незаметно переключал бы весь
    # clone_placement в режим "по цепям", ломая роли, резолвящиеся по
    # выделению. by_selection: true + непустой nets — фатал при загрузке
    # (противоречие: nets вообще не имеет смысла в режиме "по выделению").
    by_selection: bool = False

@dataclass
class Config:
    """Главный конфигурационный объект."""
    # Слой спиц (ManualSpoke-путь): 'F.Cu' | 'B.Cu'. У clone_placements —
    # свой layer/mirror per-размещение, это поле их не касается.
    layer: str = 'F.Cu'
    templates: Dict[str, SpokeTemplate] = field(default_factory=dict)
    thermal_via_array: ThermalViaArrayConfig = field(default_factory=ThermalViaArrayConfig)
    rules: List[Rule] = field(default_factory=list)
    clone_placements: List[ClonePlacement] = field(default_factory=list)
    place_components: bool = True
    skip_existing_components: bool = False
    # Параметры поиска свободного места -- сейчас используются только для
    # термовиа (у power/GND via ручное позиционирование, поиска нет).
    via_keepout_clearance_mm: float = 0.2
    via_search_step_mm: float = 0.1
    via_search_max_radius_mm: float = 3.0
    via_search_n_directions: int = 8
    # Для anchor_sheet (см. ClonePlacement) — словарь {uuid: Sheetname}
    # строится прямым парсингом *.kicad_sch (sexpdata, тот же формат, что
    # уже читает cloner), НЕ через kipy — см. обсуждение: sheet_path.
    # path_human_readable сломан в этой версии KiCad, а UUID из kipy
    # (path[:-1]) эмпирически подтверждены совпадающими с uuid в
    # (sheet ...) блоках .kicad_sch. schematic_dir — папка, где лежат
    # все *.kicad_sch проекта (путь относительно самого YAML-конфига,
    # как и templates_file); schematic_files — точечные добавки для
    # листов "на отшибе", не лежащих в schematic_dir.
    schematic_dir: Optional[str] = None
    schematic_files: List[str] = field(default_factory=list)
    # Явный override пути к файлам реестра — по умолчанию выводится из
    # имени САМОГО конфига (registry_path_for_config), что рвётся при
    # переименовании конфига (реестр молча переезжает на новый путь).
    # Пути — относительно самого этого YAML, как и templates_file.
    registry_path: Optional[str] = None
    track_registry_path: Optional[str] = None
    # Вычисляется в load_config из schematic_dir/schematic_files — НЕ
    # читается из YAML напрямую. {uuid: Sheetname}, пусто если ни
    # schematic_dir, ни schematic_files не заданы (и anchor_sheet тогда
    # использовать нельзя — см. фатал в validation.py).
    sheet_names: Dict[str, str] = field(default_factory=dict)

    @property
    def anchor_refs(self) -> set:
        """Все якорные ref конфига: правила спиц + термовиа."""
        out = {r.anchor_ref for r in self.rules if r.anchor_ref}
        if self.thermal_via_array.enabled and self.thermal_via_array.anchor_ref:
            out.add(self.thermal_via_array.anchor_ref)
        return out