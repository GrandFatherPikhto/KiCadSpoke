# decap_placer/optimization/factory.py
from .interfaces import IOptimizer
from .heuristic_optimizer import HeuristicOptimizer
from .nlp_optimizer import NLPOptimizer

class OptimizerFactory:
    @staticmethod
    def create(optimizer_type: str, adapter, config) -> IOptimizer:
        if optimizer_type == "heuristic":
            return HeuristicOptimizer(adapter, config)
        elif optimizer_type == "nlp":
            # позже реализуем
            return NLPOptimizer(adapter, config)
        else:
            raise ValueError(f"Unknown optimizer type: {optimizer_type}")