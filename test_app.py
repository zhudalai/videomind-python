import uvicorn
from videomind.interface import app

print('App created successfully')
print('Routes:', [r.path for r in app.routes])