import os

def write_file(rel_path, content):
    full_path = os.path.join(r'D:\SM_Server\MarketTool\FnOQuantAI', rel_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(content.strip() + '\n')
    print(f'WROTE: {rel_path} ({len(content)} bytes)')
