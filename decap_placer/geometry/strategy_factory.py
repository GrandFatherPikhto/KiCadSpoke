# decap_placer/geometry/strategy_factory.py
from .strategies import PlacementStrategy, RadialStrategy, OrthogonalStrategy, FixedStrategy, BoundaryStrategy

class StrategyFactory:
    @staticmethod
    def create(mode: str, fixed_angle_deg: float = 0.0) -> PlacementStrategy:
        if mode == "radial":
            return RadialStrategy()
        elif mode == "orthogonal":
            return OrthogonalStrategy()
        elif mode == "fixed":
            return FixedStrategy()
        elif mode == "boundary":
            return BoundaryStrategy()
        else:
            raise ValueError(f"Неизвестный rotation_mode: {mode}")