from videomind.application.task_orchestration.celery import celery_app
print('Celery app imports OK')
print('Tasks registered:', list(celery_app.tasks.keys())[:10])