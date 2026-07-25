from alembic.config import Config
import os

# Check if there's a alembic.ini in current dir
print('CWD:', os.getcwd())
cfg = Config('alembic.ini')
print('Found config file:', cfg.config_file_name)
print('script_location:', cfg.get_main_option('script_location'))
print('version_locations:', cfg.get_main_option('version_locations'))