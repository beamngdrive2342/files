import os
from pathlib import Path

def analyze_solutions():
    """Анализирует какие номера не скачались или пустые"""
    
    solutions_dir = Path("solutions/algebra")
    
    if not solutions_dir.exists():
        print("❌ Папка solutions/algebra не найдена!")
        return
    
    # Получаем все номера папок
    folders = sorted([int(f.name) for f in solutions_dir.iterdir() 
                     if f.is_dir() and f.name.isdigit()])
    
    print(f"📊 АНАЛИЗ РЕШЕНИЙ")
    print(f"═" * 50)
    print(f"✅ Найдено папок: {len(folders)}")
    print()
    
    # Пропущенные номера
    missing = []
    for i in range(1, max(folders) + 1):
        if i not in folders:
            missing.append(i)
    
    # Пустые папки
    empty_folders = []
    for folder_num in folders:
        folder_path = solutions_dir / str(folder_num)
        files = list(folder_path.glob("*.png"))
        if len(files) == 0:
            empty_folders.append(folder_num)
    
    # Результаты
    print(f"📌 ПРОПУЩЕННЫЕ НОМЕРА:")
    print(f"─" * 50)
    if missing:
        print(f"Всего пропущено: {len(missing)}")
        print(f"Номера: {missing[:20]}{'...' if len(missing) > 20 else ''}")
        print()
        
        # Диапазоны пропущенных
        if missing:
            ranges = []
            start = missing[0]
            end = missing[0]
            
            for num in missing[1:]:
                if num == end + 1:
                    end = num
                else:
                    if start == end:
                        ranges.append(f"{start}")
                    else:
                        ranges.append(f"{start}-{end}")
                    start = num
                    end = num
            
            if start == end:
                ranges.append(f"{start}")
            else:
                ranges.append(f"{start}-{end}")
            
            print(f"Диапазоны: {', '.join(ranges)}")
    else:
        print("✅ Все номера скачаны!")
    
    print()
    print(f"📌 ПУСТЫЕ ПАПКИ:")
    print(f"─" * 50)
    if empty_folders:
        print(f"Всего пустых: {len(empty_folders)}")
        print(f"Номера: {empty_folders}")
    else:
        print("✅ Нет пустых папок!")
    
    print()
    print(f"📌 СТАТИСТИКА:")
    print(f"─" * 50)
    print(f"Скачано папок: {len(folders)}")
    print(f"Пропущено: {len(missing)}")
    print(f"Пустых: {len(empty_folders)}")
    
    # Сохраняем список для пересчитывания
    if missing:
        print()
        print(f"💾 КОМАНДА ДЛЯ ПЕРЕСЧИТЫВАНИЯ:")
        print(f"─" * 50)
        
        # Создаем команду
        ranges_str = ", ".join([f"{r}" for r in ranges])
        print(f"python download_solutions.py --start {missing[0]} --end {missing[-1]} --subject algebra")

if __name__ == "__main__":
    analyze_solutions()