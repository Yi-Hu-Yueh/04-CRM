from pathlib import Path
import os
import sys
from PyInstaller.utils.hooks import collect_submodules,collect_data_files,copy_metadata

root=Path(SPECPATH).parent.resolve()
hidden=collect_submodules('crm',filter=lambda name:not name.startswith('crm.tests'))
hidden += ['config.settings','config.urls','config.wsgi','django.db.backends.sqlite3']
data=[]
for folder in ['templates','static','portable']:
    data.append((str(root/folder),folder))
data += collect_data_files('django')+collect_data_files('tzdata')+copy_metadata('waitress')
a=Analysis([str(root/'portable_main.py')],pathex=[str(root)],binaries=[],datas=data,hiddenimports=hidden,
    excludes=['crm.tests','django.contrib.gis','django.db.backends.oracle','django.db.backends.mysql','django.db.backends.postgresql','tkinter'],
    noarchive=False,optimize=0)

# Django's stock hook searches for *.db next to manage.py. Do not ship those files.
# Fail before producing an executable if a hook tries to collect private inputs.
allowed=[root/f for f in ['templates','static','portable','crm/migrations','.venv/Lib/site-packages']]
allowed += [Path(sys.base_prefix).resolve(),Path(os.environ.get('SystemRoot','C:/Windows'))/'System32']
def safe_payload(entry):
    destination,source,kind=entry
    source=Path(source).resolve()
    generated_stdlib=(root/'build'/'portable'/'CRM'/'base_library.zip').resolve()
    if source!=generated_stdlib and not any(source.is_relative_to(base) for base in allowed):
        raise RuntimeError('Unapproved data or binary input in portable build: '+str(source))
    if source.suffix.lower() in ['.db','.sqlite','.sqlite3','.key']:
        raise RuntimeError('Database or secret input in portable build: '+str(source))
    return entry
a.datas=[safe_payload(entry) for entry in a.datas]
a.binaries=[safe_payload(entry) for entry in a.binaries]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='CRM',debug=False,bootloader_ignore_signals=False,
    strip=False,upx=False,console=True,disable_windowed_traceback=False)
