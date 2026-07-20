Проверка наличия цепей

```bash
python -c "from kicadspoke.kicad.adapter import KiCadBoardAdapter; a=KiCadBoardAdapter(); a.refresh_board(); print(a.get_net_by_name('/Channel_0/DAC/+3V3_CLKVDD'))"
```