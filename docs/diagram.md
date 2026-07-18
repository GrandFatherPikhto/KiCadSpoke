graph TD
    CLI[kicadspoke_cli.py] --> Config[config.py]
    CLI --> Adapter[kicad/adapter.py]
    CLI --> Validation[validation.py]
    CLI --> Planner[placement/planner.py]
    CLI --> Executor[placement/executor/batch_executor.py]
    CLI --> Registry[registry.py]
    CLI --> Extract[template_extraction.py]
    CLI --> Undo[undo.py]
    CLI --> Constants[constants.py]

    Config --> Exceptions[exceptions.py]
    Validation --> Config
    Validation --> ComponentPool[placement/services/component_pool.py]
    Validation --> Exceptions

    Registry --> Config
    Registry --> Adapter
    Registry --> Exceptions

    Extract --> Adapter
    Extract --> Config
    Extract --> Exceptions

    Undo --> Adapter
    Undo --> Exceptions

    NetResolution[net_resolution.py] --> Exceptions
    NetResolution --> Config (использует ClonePlacement)