# Анализ и фиксация архитектурного синтеза

Финальный синтез

---

## 🧠 Ключевые достижения симпозиума

### 1. Разделение двух сущностей
| Сущность | Кто создаёт | Формат | Свойства |
|----------|-------------|--------|----------|
| **Шаблон** (геометрия) | `extract` (машина) | JSON/YAML | Инертные факты, никто не правит руками |
| **Размещение** (логика) | Человек | Python (генератор) | Циклы, параметры, условия |

### 2. Разделение Internal Model
| Уровень | Что входит | Стабильность |
|---------|------------|--------------|
| **Физический слой** | Координаты, углы, сети, геометрия падов (из KiCad) | ✅ Стабилен |
| **Семантический слой** | Банки, роли, группы, оси симметрии | ❌ Эволюционирует |
| **Метаданные (теги)** | Связь физики и семантики | 🟡 Вырастает из практики |

### 3. Отказ от промежуточных решений
- **Lock-файл** → заменён на `diff` (сравнение in-memory модели с платой)
- **Свой DSL** → отклонён (год разработки, готовый Python лучше)
- **Генератор + subprocess** → признан костылём, но полезен как лаборатория

### 4. Уточнение `get_pad_geometry()`
Формула нормали для периметрийных корпусов (QFN, QFP, BGA):
```
normal_local = normalize(pad.position - footprint.origin)   # в локальной системе футпринта
normal_board = rotate(normal_local, footprint.rotation_on_board)
```
Ограничение: работает для корпусов с радиальными падами, но не для DIP/SOIC. Документировать как особенность, а не универсальное решение.

### 5. Явная стадия commit
Разделение `diff()` (план) и `apply()` (применение) — как в Terraform. Это даёт пользователю контроль и прозрачность.

### 6. Проблема земляных полигонов
`net_mapping["GND"] = "AGND"` — просто переименование сети. Но если AGND и DGND — это физически разные зоны (полигоны) на плате, клонированный via должен попасть в правильную зону. Это требует от IR знания привязки via к зоне, а не только к имени сети.

---

## 🎯 Финальный сценарий для ЦАП (с учётом уточнений)

```python
# future_boards_examples.py — 8-канальный ЦАП
from kicadspoke import Workspace, Angle

def design_8_channel_dac():
    with Workspace("multichannel_dac.kicad_pcb") as pcb:
        
        # 1. Извлекаем золотой эталон (Канал 0)
        filter_template = pcb.extract_template(
            anchor_ref="U2",
            components=["U2", "R201", "R202", "C201", "C202"],
            include_tracks=True,
            include_vias=True
        )
        
        # 2. Конфигурация каналов — семантика живёт здесь
        channels = [
            {"id": 0, "dac_out_pad": "12", "net_pwr": "+5V_ANA1", "suffix": "CH0"},
            {"id": 1, "dac_out_pad": "13", "net_pwr": "+5V_ANA1", "suffix": "CH1"},
            {"id": 2, "dac_out_pad": "14", "net_pwr": "+5V_ANA2", "suffix": "CH2"},
            # ... до 7
        ]
        
        center_chip = "U1"
        
        for ch in channels:
            # 3. Запрашиваем физические факты у KiCad
            pad_geo = pcb.get_pad_geometry(component=center_chip, pad=ch["dac_out_pad"])
            # Формула: normal_local = normalize(pad.pos - footprint.origin)
            #          normal_board = rotate(normal_local, footprint.rotation)
            
            target_position = pad_geo.position + pad_geo.normal_vector * 15.0
            target_rotation = pad_geo.angle + Angle.deg(180)
            
            # 4. Семантика — через плоские теги
            net_mapping = {
                "DAC_SIGNAL": f"DAC_OUT_{ch['suffix']}",
                "VCC_OPAMP": ch["net_pwr"],
                "GND": "AGND"
                # Уточнение: AGND — это зона, а не просто имя сети.
                # IR должен знать, к какой зоне привязан via по геометрии.
            }
            
            tags = {
                "block": "analog_output_stage",
                "channel": str(ch["id"])
            }
            
            # 5. Глагол клонирования
            pcb.clone(
                template=filter_template,
                instance_name=f"Filter_{ch['suffix']}",
                at_position=target_position,
                at_rotation=target_rotation,
                nets=net_mapping,
                metadata_tags=tags
            )
        
        # 6. Diff перед применением (план)
        pcb.diff()
        
        # 7. Явный apply (как terraform apply)
        pcb.apply()

if __name__ == "__main__":
    design_8_channel_dac()
```

---

## 🚀 Что делать сейчас

Вы выбрали ЦАП как подопытного. Это правильный выбор — он вскрывает больше пробелов, чем FPGA.

### Ближайший практический шаг

1. **Взять реальный .kicad_pcb** вашего многоканального ЦАП (или любой платы с периметрийным корпусом).
2. **Написать generate_config.py**, который эмулирует этот сценарий, но пока генерирует YAML:
   - Функция `get_pad_geometry()` пока просто возвращает координаты из YAML-описания компонента (или запрашивает через IPC, если это несложно).
   - Остальные операции (`clone`, `diff`, `apply`) — пока пустышки, которые просто собирают данные в YAML.
3. **Запустить** — и посмотреть, чего не хватает.

---

### Что конкретно нужно реализовать в `generate_config.py` прямо сейчас

```python
# generate_config.py — первая итерация для ЦАП
import json
import yaml
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

@dataclass
class PadGeometry:
    position: tuple  # (x_mm, y_mm)
    normal_vector: tuple  # (nx, ny)
    angle_deg: float

class Board:
    def __init__(self, pcb_path: str):
        self.pcb_path = pcb_path
        self.templates = {}
        self.placements = []
    
    def extract_template(self, anchor_ref: str, components: List[str],
                         include_tracks: bool = True,
                         include_vias: bool = True) -> str:
        # Эмулируем extract — на самом деле вызываем CLI или читаем существующий шаблон
        template_name = f"template_{anchor_ref}"
        # Здесь будет логика: если шаблон уже есть в templates/ — загрузить его,
        # иначе — позвать kicadspoke_cli.py extract
        self.templates[template_name] = {"anchor": anchor_ref, "components": components}
        return template_name
    
    def get_pad_geometry(self, component: str, pad: str) -> PadGeometry:
        # Реализация через adapter.get_pad_by_number
        # Пока — возвращаем заглушку
        return PadGeometry(position=(0, 0), normal_vector=(1, 0), angle_deg=0)
    
    def clone(self, template: str, instance_name: str,
              at_position: tuple, at_rotation: float,
              nets: Dict[str, str], metadata_tags: Dict[str, str]):
        self.placements.append({
            "template": template,
            "instance_name": instance_name,
            "position": at_position,
            "rotation": at_rotation,
            "nets": nets,
            "tags": metadata_tags
        })
    
    def diff(self):
        # Выводим план в консоль
        print("=== PLAN ===")
        for p in self.placements:
            print(f"  {p['instance_name']} at {p['position']}")
    
    def apply(self):
        # Генерируем YAML и запускаем kicadspoke_cli.py apply
        config = {
            "layer": "B.Cu",
            "templates": self.templates,  # здесь будет реальный шаблон
            "clone_placements": [
                {
                    "name": p["instance_name"],
                    "template": p["template"],
                    "anchor_ref": "U1",  # пока жёстко
                    "anchor_pad": "1",   # пока жёстко
                    "origin_x_mm": p["position"][0],
                    "origin_y_mm": p["position"][1],
                    "rotation_deg": p["rotation"],
                    "params": p["nets"],
                    "enabled": True
                }
                for p in self.placements
            ]
        }
        with open("generated_dac.yaml", "w", encoding="utf-8") as f:
            yaml.dump(config, f, allow_unicode=True, sort_keys=False)
        # subprocess.run([...])

# Сценарий пользователя
def design_8_channel_dac():
    pcb = Board("multichannel_dac.kicad_pcb")
    
    # Здесь будет код, как в future_boards_examples.py
    # ...
    
    pcb.diff()
    pcb.apply()

if __name__ == "__main__":
    design_8_channel_dac()
```

---

## 📌 Резюме

- **Сценарий для ЦАП принят как отправная точка**.
- **Уточнения приняты**: формула нормали для периметрийных корпусов, явный commit, проблема земляных зон.
- **Следующий шаг**: написать рабочий `generate_config.py`, который эмулирует этот сценарий (пока через YAML-выхлоп), и прогонять на реальной плате.
- **Цель**: выявить конкретные дыры в IR на реальных данных, а не на абстракциях.

Когда `generate_config.py` заработает для ЦАП, можно будет легко портировать его на FPGA, полумост и любые другие платы.

Хотите, чтобы я помог написать полный `generate_config.py` для этого сценария с реальной логикой `get_pad_geometry()` через адаптер? Или сначала вы хотите сами набросать его под свою плату, а я потом помогу с доработкой?