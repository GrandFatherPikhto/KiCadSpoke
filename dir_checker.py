import os
import hashlib
import argparse  # Добавили для работы с аргументами командной строки
import yaml
from pathlib import Path

def calculate_sha256(file_path):
    """Вычисляет SHA-256 хэш файла для точного сравнения содержимого."""
    sha256_hash = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception as e:
        return f"ERROR: {str(e)}"

def should_ignore(path, ignore_list):
    """Проверяет, входит ли файл или какая-то из его родительских папок в список игнорируемых."""
    parts = Path(path).parts
    return any(ignored in parts for ignored in ignore_list)

def get_filtered_files(dir_path, extensions, ignore_list):
    """Собирает все файлы из директории с учетом фильтров по расширениям и ignore-списка."""
    file_dict = {}
    base_path = Path(dir_path).resolve()
    
    if not base_path.exists():
        print(f"⚠️ Предупреждение: Директория не найдена: {dir_path}")
        return file_dict

    for root, _, files in os.walk(base_path):
        for file in files:
            full_path = Path(root) / file
            
            if should_ignore(full_path, ignore_list):
                continue
                
            if extensions and full_path.suffix not in extensions:
                continue
                
            relative_path = full_path.relative_to(base_path)
            file_dict[str(relative_path)] = full_path
            
    return file_dict

def compare_directories(dir1, dir2, extensions, ignore_list):
    """Сравнивает две директории и выводит различия."""
    print(f"\n=== Сравнение директорий ===")
    print(f"Папка 1: {dir1}")
    print(f"Папка 2: {dir2}")
    
    files1 = get_filtered_files(dir1, extensions, ignore_list)
    files2 = get_filtered_files(dir2, extensions, ignore_list)
    
    all_relative_paths = set(files1.keys()).union(set(files2.keys()))
    diff_count = 0
    
    for rel_path in sorted(all_relative_paths):
        if rel_path not in files2:
            print(f"❌ Только в Папке 1: {rel_path}")
            diff_count += 1
        elif rel_path not in files1:
            print(f"❌ Только в Папке 2: {rel_path}")
            diff_count += 1
        else:
            hash1 = calculate_sha256(files1[rel_path])
            hash2 = calculate_sha256(files2[rel_path])
            
            if hash1 != hash2:
                print(f"🔄 Различается содержимое: {rel_path}")
                diff_count += 1
                
    if diff_count == 0:
        print("✅ Директории абсолютно идентичны (с учетом фильтров).")

def compare_custom_pairs(pairs):
    """Сравнивает отдельные пары файлов, жестко заданные в конфигурации."""
    if not pairs:
        return
        
    print(f"\n=== Сравнение отдельных пар файлов ===")
    for pair in pairs:
        f1 = Path(pair.get('file1'))
        f2 = Path(pair.get('file2'))
        
        if not f1.exists() or not f2.exists():
            print(f"⚠️ Ошибка: Один из файлов пары не найден ({f1} <-> {f2})")
            continue
            
        hash1 = calculate_sha256(f1)
        hash2 = calculate_sha256(f2)
        
        if hash1 == hash2:
            print(f"✅ Совпадают: {f1.name} == {f2.name}")
        else:
            print(f"🔄 РАЗЛИЧАЮТСЯ: {f1} <-> {f2}")

def main():
    # Настраиваем парсер аргументов командной строки
    parser = argparse.ArgumentParser(description="Скрипт для сравнения директорий и файлов по YAML-конфигу.")
    parser.add_argument(
        "-c", "--config", 
        type=str, 
        default="config.yaml", 
        help="Путь к YAML-файлу конфигурации (по умолчанию: config.yaml)"
    )
    
    args = parser.parse_args()
    config_file = args.config

    if not os.path.exists(config_file):
        print(f"❌ Файл конфигурации '{config_file}' не найден!")
        return

    print(f"📖 Используется конфигурация: {config_file}")

    with open(config_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    dir_cfg = config.get("directories", {})
    dir1 = dir_cfg.get("dir1")
    dir2 = dir_cfg.get("dir2")
    
    filters = config.get("filters", {})
    extensions = filters.get("extensions", [])
    ignore_list = filters.get("ignore", [])
    
    custom_pairs = config.get("custom_file_pairs", [])

    if dir1 and dir2:
        compare_directories(dir1, dir2, extensions, ignore_list)
    else:
        print("ℹ️ Сравнение директорий пропущено (не заданы dir1 или dir2 в конфиге).")

    if custom_pairs:
        compare_custom_pairs(custom_pairs)

if __name__ == "__main__":
    main()
