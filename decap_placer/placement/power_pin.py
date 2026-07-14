# decap_placer/placement/power_pin.py
from ..config import Config, SpokeComponent, Spoke

def resolve_power_pin_facing(component: SpokeComponent, spoke: Spoke, cfg: Config) -> str:
    """
    Разрешает direction "pad"/"away" с приоритетом: компонент -> спица ->
    глобальный конфиг. Тот же принцип, что и у via: (локальное
    определение побеждает, если задано).
    """
    if component.power_pin_facing is not None:
        return component.power_pin_facing
    if spoke.power_pin_facing is not None:
        return spoke.power_pin_facing
    return cfg.power_pin_facing