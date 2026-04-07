content = open('.github/workflows/deploy-dev.yml', encoding='utf-8').read() 
idx = content.find('  notify-dev:') 
if idx != -1: content = content[:idx].rstrip() + '\n' 
open('.github/workflows/deploy-dev.yml', 'w', encoding='utf-8').write(content) 
print('dev done') 
