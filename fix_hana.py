import os 
f = open('backend/app/db/hana.py', encoding='utf-8').read() 
old = 'for _ in range(POOL_SIZE):' 
new = 'if os.getenv(\"ENVIRONMENT\",\"local\") == \"local\":\n        return\n    for _ in range(POOL_SIZE):' 
f = f.replace(old, new) 
open('backend/app/db/hana.py', 'w', encoding='utf-8').write(f) 
print('done') 
