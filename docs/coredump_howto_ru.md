# Как поймать и разобрать краш-дамп KiCad (Windows + Linux)

KiCadSpoke работает через IPC-API живого KiCad (`kipy`), поэтому падения самого KiCad — не абстрактная
угроза, а то, с чем реально приходится разбираться (см. `techdocs/issues/` — историю двух таких багов).
Эта памятка — не общая теория, а то, что реально сработало при живой охоте за краш-дампом на обеих ОС.

## Windows

Инструментарий — [HuntProc](https://github.com/GrandFatherPikhto/HuntProc) поверх ProcDump + WER.

1. **Ловим дамп**: `ProcDump` в режиме first-chance-мониторинга на `kicad.exe`, либо WER сам пишет
   полный дамп при падении (см. `%LOCALAPPDATA%\CrashDumps`, если включён `LocalDumps` в реестре).
   HuntProc автоматизирует именно это — сидит и ждёт, забирает дамп сразу после падения.
2. **Символы** — публичный символьный сервер KiCad:
   `SRV*<локальный кэш>*https://symbols.kicad.org/kicad-stable`.
3. **Разбор** — `cdb`/WinDbg:
   ```
   cdb -z <dump.zip\dump.dmp> -y SRV*C:\symbols*https://symbols.kicad.org/kicad-stable -c "!analyze -v; q"
   ```
   `!analyze -v` даёт вердикт (`FAILURE_BUCKET_ID`), адрес исключения, символизированный стек — этого
   обычно достаточно для репорта, без ручного разбора дизассемблера.

## Linux

Разбор реальной охоты (KiCad 10.0.5, Flatpak `org.kicad.KiCad`, Ubuntu). Три места, где можно
споткнуться — все настоящие, все встретились по пути.

### 0. Определить, как установлен KiCad

```bash
flatpak list --all | grep -i kicad   # Flatpak?
dpkg -l | grep -i kicad               # или родной apt-пакет?
```
На системе может оказаться сразу оба варианта (Flatpak + старый apt-пакет). Дальнейшие шаги — под
Flatpak; для apt-пакета всё проще (символы через `apt install kicad-dbgsym` или debuginfod, без
плясок с сэндбоксом).

### 1. Символы

Для Flatpak — отдельное `.Debug`-расширение той же ветки. **Важно**: оно НЕ показывается в обычном
`flatpak remote-ls` (Flatpak по умолчанию скрывает debug-расширения из листинга) — ставить надо по
точному имени, не полагаясь на то, что `remote-ls | grep debug` ничего не нашёл:

```bash
flatpak install <remote> org.kicad.KiCad.Debug//stable   # имя remote — своё, см. `flatpak remotes`
```

```bash
# Проверить, что вообще есть на remote для kicad (включая debug-расширения)
flatpak remote-ls <remote> | grep -i kicad
```

Если расширения нет вообще (или лень ставить ~2 ГБ) — fallback на `debuginfod` (Flathub держит свой
сервер, `gdb` сам подтягивает по build-id, ничего ставить не надо):
```bash
export DEBUGINFOD_URLS="https://debuginfod.flathub.org/"
```

### 2. Ловля самого core

Первая, бесплатная проверка (работает всегда, без всякой настройки) — ядро само пишет segfault в
журнал, даже если core-файл никуда не попал:
```bash
journalctl -k | grep -i segfault
# ... kernel: kicad[26482]: segfault at 0 ip ... in _eeschema.kiface[...] ...
```
Уже даёт адрес и модуль — полезно как быстрый сигнал "оно и правда падает", но без символов.

Для полного core с бэктрейсом: на Ubuntu краши по умолчанию идут через **apport**
(`cat /proc/sys/kernel/core_pattern` — увидишь `.../apport ...`). С Flatpak-сэндбоксом apport часто
не справляется (namespace текущего процесса отличается от того, что видит обработчик) — в `/var/crash/`
может быть пусто, даже когда `journalctl -k` честно показал сегфолт. Обход — направить `core_pattern`
на простой абсолютный путь (кладётся ядром в файловой системе САМОГО падающего процесса, без пайпа
во внешний обработчик — сэндбокс тут не мешает, если путь у процесса виден, например внутри `$HOME`):

```bash
mkdir -p ~/coredumps
echo "$HOME/coredumps/core.%e.%p.%t" | sudo tee /proc/sys/kernel/core_pattern
```

**Критично**: `ulimit -c unlimited` нужно выставить В ТОМ ЖЕ терминале, из которого запускается
KiCad — если запускать через иконку/gnome-shell, процесс наследует лимиты systemd-сессии, а не твоего
шелла, и `ulimit -c 0` (дефолт на многих системах) тихо запретит запись core вообще:

```bash
ulimit -c unlimited
flatpak run org.kicad.KiCad
```

Дальше — воспроизвести краш как обычно и проверить `ls -la ~/coredumps/`.

### 3. Анализ

Для Flatpak-приложения gdb надо гонять ВНУТРИ того же сэндбокса — тогда пути к бинарникам/библиотекам
резолвятся сами, руками искать `/var/lib/flatpak/app/...` не нужно:

```bash
flatpak run --command=gdb org.kicad.KiCad -batch -ex "bt full" -ex quit -c ~/coredumps/<файл>
```
Для нативного (apt) пакета — обычный `gdb /usr/bin/kicad <corefile>`.

### Нужен ли отдельный демон-охотник (аналог HuntProc)?

Нет — ловля самого краша на Linux синхронна и встроена в ядро (`core_pattern` срабатывает в момент
падения сам, никакой службы держать не надо, в отличие от Windows, где WER/ProcDump и есть отдельный
сервис). Единственное, что реально экономит время при частых повторных охотах — маленький вотчер
(`inotifywait` на директорию с core + автозапуск `gdb -batch -ex "bt full"` на новый файл), чтобы не
гонять шаг 3 руками каждый раз. Для разовой/редкой охоты — оверинжиниринг, ручных шагов выше достаточно.
