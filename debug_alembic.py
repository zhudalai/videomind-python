from alembic.config import Config
cfg = Config('alembic.ini')
print('script_location:', cfg.get_main_option('script_location'))
print('version_locations:', cfg.get_main_option('version_locations'))