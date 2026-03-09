with open('handlers.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    if '=====' in line:
        print(f'{i}: {line.strip()}')
