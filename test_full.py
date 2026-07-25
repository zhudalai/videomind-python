import uvicorn
from videomind.interface import app
from videomind.observability.metrics import get_metrics_app

print('=== FastAPI App ===')
print('Routes:', [r.path for r in app.routes])

print('\n=== Metrics App ===')
metrics_app = get_metrics_app()
print('Metrics app created successfully')

print('\n=== All imports OK ===')