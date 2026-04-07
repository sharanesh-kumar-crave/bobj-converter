content = open('.github/workflows/deploy-prod.yml', encoding='utf-8').read() 
idx = content.find('  notify-prod:') 
if idx != -1: content = content[:idx].rstrip() + '\n' 
open('.github/workflows/deploy-prod.yml', 'w', encoding='utf-8').write(content) 
print('prod done') 
