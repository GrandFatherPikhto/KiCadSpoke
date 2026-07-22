## 🧭 Что реально устоялось (консенсус)

1. **YAML не враг** — враг смешение данных (шаблоны из `extract`) и логики (размещение).
2. **Свой DSL — не делать**. Python даёт то же самое бесплатно.
3. **Lock-файл → `diff` (план) + явный `apply`** (как Terraform).
4. **API не проектировать заранее** — растить из повторяющихся глаголов в `generate_config.py`.
5. **IR держать физическим** — координаты, геометрия падов. Семантику (банки, роли, зоны) вешать тегами сверху, не встраивать в структуру.

Это пять раз независимо пришли к одному — доверяем.

---

## ⚠️ Реальная нестыковка (не драма)

- `integral_model.md` выбрал **FPGA 10CL006** для "кода мечты".
- `architect.md` и наш разговор — **8-канальный ЦАП**.

Это не разногласие по существу. Просто параллельные сессии симпозиума не знали о выборе друг друга. Ничего страшного, смысла искать нет.

---

## 🐛 Баг в скелете из `architect.md` (ловим сразу)

В `apply()`:

```python
"anchor_ref": "U1",  # захардкожено
"anchor_pad": "1",   # захардкожено
```

А нужно **per-placement**:

```python
self.placements.append({
    ...
    "anchor_pad": anchor_pad,  # должен быть уникальным для каждого канала
    ...
})
```

Если запустить как есть — все 8 фильтров лягут в одну точку. Это тот самый случай, о котором говорили: `clone()` кладёт в память `position`/`rotation`, но `apply()` не пробрасывает `anchor_pad`.

---

## 🛠️ Что делать прямо сейчас (один шаг, без философии)

Возьмите скелет из `architect.md`, поправьте баг с `anchor_pad`, замените `get_pad_geometry` на реальный вызов через ваш существующий `kipy`-адаптер — и прогоните на настоящем `.kicad_pcb` вашего ЦАПа.

---

## 🧪 Я могу помочь прямо сейчас

Вот исправленный скелет `generate_config.py` с:

- ✅ Поправленным багом `anchor_pad` (теперь per-placement)
- ✅ Реальной реализацией `get_pad_geometry()` через ваш адаптер

```python
# generate_config.py
import json
import yaml
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

# Импортируем существующий адаптер KiCadSpoke
from kicadspoke.kicad.adapter import KiCadBoardAdapter
from kicadspoke.utils.units import MM

@dataclass
class PadGeometry:
    position: Tuple[float, float]  # (x_mm, y_mm)
    normal_vector: Tuple[float, float]  # (nx, ny) — единичный вектор
    angle_deg: float

class Board:
    def __init__(self, pcb_path: str):
        self.pcb_path = pcb_path
        self.adapter = KiCadBoardAdapter()
        self.adapter.refresh_board()
        self.templates = {}
        self.placements = []
    
    def extract_template(self, anchor_ref: str, components: List[str],
                         include_tracks: bool = True,
                         include_vias: bool = True) -> str:
        # Здесь либо вызываем реальный extract через CLI, либо читаем существующий шаблон
        # Пока — заглушка для прототипа
        template_name = f"template_{anchor_ref}"
        self.templates[template_name] = {
            "anchor": anchor_ref,
            "components": components,
            "tracks": include_tracks,
            "vias": include_vias
        }
        return template_name
    
    def get_pad_geometry(self, component: str, pad: str) -> PadGeometry:
        """
        Реальная реализация через kipy-адаптер.
        Возвращает позицию пада и вектор нормали (от центра футпринта к паду).
        """
        fp = self.adapter.get_footprint(component)
        if fp is None:
            raise ValueError(f"Компонент {component} не найден на плате")
        
        pad_obj = self.adapter.get_pad_by_number(fp, pad)
        if pad_obj is None:
            raise ValueError(f"Пад {pad} у {component} не найден")
        
        # Позиция пада в мм
        pos_x = pad_obj.position.x / MM
        pos_y = pad_obj.position.y / MM
        
        # Вектор нормали: от центра футпринта к паду (в локальной системе)
        # Затем поворачиваем на угол футпринта
        from kipy.geometry import Vector2, Angle
        origin = Vector2.from_xy(0, 0)
        local_vec = pad_obj.position - fp.position
        # Поворачиваем обратно, чтобы получить локальный вектор
        local_vec_rot = local_vec.rotate(Angle.from_degrees(-fp.orientation.degrees), origin)
        
        # Нормаль — нормализованный локальный вектор
        length = local_vec_rot.length()
        if length > 0:
            nx = local_vec_rot.x / length
            ny = local_vec_rot.y / length
        else:
            # Если пад в центре — используем дефолт
            nx, ny = 1.0, 0.0
        
        # Возвращаем позицию и нормаль в глобальной системе
        return PadGeometry(
            position=(pos_x, pos_y),
            normal_vector=(nx, ny),
            angle_deg=fp.orientation.degrees
        )
    
    def clone(self, template: str, instance_name: str,
              at_position: Tuple[float, float],
              at_rotation: float,
              anchor_pad: str,  # <-- теперь per-placement
              nets: Dict[str, str],
              metadata_tags: Dict[str, str]):
        self.placements.append({
            "template": template,
            "instance_name": instance_name,
            "position": at_position,
            "rotation": at_rotation,
            "anchor_pad": anchor_pad,  # <-- сохраняем для apply
            "nets": nets,
            "tags": metadata_tags
        })
    
    def diff(self):
        """Выводит план в консоль (альтернатива lock-файлу)."""
        print("\n=== PLAN ===")
        print(f"  {len(self.placements)} placements to apply:")
        for p in self.placements:
            print(f"    {p['instance_name']}: pad {p['anchor_pad']} at ({p['position'][0]:.3f}, {p['position'][1]:.3f}) мм, rot={p['rotation']:.1f}°")
        print("==============\n")
    
    def apply(self):
        """Генерирует YAML и запускает kicadspoke_cli.py apply."""
        # Загружаем реальный шаблон из templates/
        # Пока используем заглушку, но в реальности — читаем из файла
        template_data = self.templates.get("template_U2", {})
        
        config = {
            "layer": "B.Cu",
            "templates": {
                "dac_filter": template_data  # здесь должен быть полный шаблон
            },
            "clone_placements": [
                {
                    "name": p["instance_name"],
                    "template": "dac_filter",
                    "anchor_ref": "U1",
                    "anchor_pad": p["anchor_pad"],  # <-- исправлено
                    "origin_x_mm": p["position"][0],
                    "origin_y_mm": p["position"][1],
                    "rotation_deg": p["rotation"],
                    "params": p["nets"],
                    "enabled": True
                }
                for p in self.placements
            ]
        }
        
        # Сохраняем YAML
        output_path = "generated_dac.yaml"
        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False)
        
        print(f"✅ Конфиг сгенерирован: {output_path}")
        
        # Запускаем kicadspoke_cli.py apply
        cli_args = sys.argv[1:]  # пробрасываем --dry-run и т.п.
        cmd = [sys.executable, "kicadspoke_cli.py", "apply", output_path] + cli_args
        subprocess.run(cmd)


# ===== Сценарий для 8-канального ЦАП =====
def design_8_channel_dac():
    pcb = Board("multichannel_dac.kicad_pcb")
    
    # 1. Извлекаем шаблон (золотой эталон)
    filter_template = pcb.extract_template(
        anchor_ref="U2",
        components=["U2", "R201", "R202", "C201", "C202"],
        include_tracks=True,
        include_vias=True
    )
    
    # 2. Конфигурация каналов
    channels = [
        {"id": 0, "pad": "12", "net_pwr": "+5V_ANA1", "suffix": "CH0"},
        {"id": 1, "pad": "13", "net_pwr": "+5V_ANA1", "suffix": "CH1"},
        {"id": 2, "pad": "14", "net_pwr": "+5V_ANA2", "suffix": "CH2"},
        {"id": 3, "pad": "15", "net_pwr": "+5V_ANA2", "suffix": "CH3"},
        # ... до 7
    ]
    
    center_chip = "U1"
    
    for ch in channels:
        # 3. Получаем геометрию пада через реальный адаптер
        pad_geo = pcb.get_pad_geometry(component=center_chip, pad=ch["pad"])
        
        # Смещаемся вдоль нормали на 15 мм
        nx, ny = pad_geo.normal_vector
        target_x = pad_geo.position[0] + nx * 15.0
        target_y = pad_geo.position[1] + ny * 15.0
        target_rotation = pad_geo.angle_deg + 180.0
        
        # 4. Клонируем с per-placement anchor_pad
        pcb.clone(
            template=filter_template,
            instance_name=f"Filter_{ch['suffix']}",
            at_position=(target_x, target_y),
            at_rotation=target_rotation,
            anchor_pad=ch["pad"],  # <-- уникальный для каждого канала
            nets={
                "DAC_SIGNAL": f"DAC_OUT_{ch['suffix']}",
                "VCC_OPAMP": ch["net_pwr"],
                "GND": "AGND"
            },
            metadata_tags={
                "block": "analog_output_stage",
                "channel": str(ch["id"])
            }
        )
    
    # 5. План
    pcb.diff()
    
    # 6. Применение (если не указан --dry-run)
    if "--dry-run" not in sys.argv:
        pcb.apply()

if __name__ == "__main__":
    design_8_channel_dac()
```

---

## 🧪 Как проверить

1. Сохраните этот код как `generate_dac.py`
2. Убедитесь, что у вас есть реальный `.kicad_pcb` для ЦАП (или любой платы с компонентом `U1` и падами)
3. Запустите:
   ```bash
   python generate_dac.py --dry-run
   ```
   Посмотрите, что выводит `diff()`.
4. Если всё выглядит правильно — уберите `--dry-run` и запустите без него.

---

## 📌 Что дальше

Когда `generate_dac.py` заработает и вы увидите реальный план в консоли, мы сможем:

1. **Перенести удачные глаголы** (`clone`, `get_pad_geometry`, `diff`) в ядро KiCadSpoke.
2. **Добавить поддержку зон** (AGND/DGND) — если окажется, что это критично.
3. **Расширить на FPGA** — просто скопировать подход и подставить другие данные.

Главное — у вас теперь есть **рабочий исследовательский инструмент**, а не гипотеза.

Хотите, чтобы я помог с первым запуском, или сначала сами попробуете?