# Набор актуальных комманд

## Расстановка конденсаторов питания для 10CL006Y3144C8G

```bash
python kicadspoke_cli.py .\10CL006YE144C8G.yaml --verbose --log-file logs/placer.log --verbose
```

## Отмена расстановки

```bash
python kicadspoke_cli.py undo --verbose
```

## Клонирование и применение шаблонов

## Чтение шаблона

```bash
python kicadspoke_cli.py extract --name pi_filter_vccint --output pi_filter_vccint.yaml --verbose
```

## Применение шаблона

```bash
python kicadspoke_cli.py apply .\templates\pi_filter_vccio.yaml --clone-placement pi_filter_vccio    
```

## Тестирование `KiCad` на краши

### Тест на чтение

```bash
python -m kicadspoke.diagnostics.diagnose_first_write_crash --until 8 
```

### Тест на чтение/запись

```bash
python -m kicadspoke.diagnostics.diagnose_first_write_crash
```

### Тест гипотезы гонки

```bash
python -m kicadspoke.diagnostics.diagnose_first_write_crash --delay 30
```


