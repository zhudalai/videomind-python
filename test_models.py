import sys
sys.path.insert(0, 'd:/shu_e/Documents/Video MInd python/src')
from videomind.infrastructure.storage.models import Base
print('Models imported successfully')
print('Tables:', list(Base.metadata.tables.keys()))