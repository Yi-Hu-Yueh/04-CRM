"""Build the approved application payload, never the project directory or database."""
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from crm.packaging import inspect_release,release_metadata,create_archive

def collect_notices():
    sections=['04-CRM third-party software notices\nPython runtime and application dependencies are included in CRM.exe.\n']
    for name in ['Django','asgiref','sqlparse','tzdata','waitress','pyinstaller','altgraph','packaging','pyinstaller-hooks-contrib','pefile','pywin32-ctypes','setuptools']:
        dist=importlib.metadata.distribution(name)
        sections.append(f'\n=== {dist.metadata["Name"]} {dist.version} ===\n')
        licenses=[file for file in dist.files or [] if any(part.lower() in ['licenses','license_files'] for part in file.parts) or file.name.lower() in ['license','license.txt','license.rst','license.md','copying.txt','copying','copying.rst']]
        for file in licenses:
            p=dist.locate_file(file)
            if p.is_file():sections.append(f'\n--- {file} ---\n'+p.read_text(encoding='utf-8',errors='replace'))
    for name in ['LICENSE.txt','LICENSE']:
        p=Path(sys.base_prefix)/name
        if p.is_file():sections.append('\n=== Python license ===\n'+p.read_text(encoding='utf-8',errors='replace'));break
    sections.append('\nSQLite is in the public domain. https://sqlite.org/copyright.html\n')
    (ROOT/'portable'/'THIRD-PARTY-NOTICES.txt').write_text('\n'.join(sections),encoding='utf-8')

def main():
    collect_notices()
    env=os.environ.copy()
    temp=ROOT/'.qa'/'tmp';temp.mkdir(parents=True,exist_ok=True)
    env.update(TEMP=str(temp),TMP=str(temp),CRM_DATA_DIR=str(ROOT/'.qa'/'build-runtime'),DJANGO_SETTINGS_MODULE='config.settings',PYTHONIOENCODING='utf-8',
               PYINSTALLER_CONFIG_DIR=str(ROOT/'.qa'/'pyinstaller-cache'))
    windows=Path(os.environ.get('SystemRoot','C:/Windows'))
    env['PATH']=os.pathsep.join(str(p) for p in [Path(sys.executable).parent,Path(sys.base_prefix),Path(sys.base_prefix)/'DLLs',windows/'System32',windows])
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean',
             '--distpath',str(ROOT/'dist'/'portable'),'--workpath',str(ROOT/'build'/'portable'),str(ROOT/'tools'/'CRM.spec')]
    subprocess.run(command,cwd=ROOT,env=env,check=True)
    output=ROOT/'dist'/'portable'
    # PyInstaller's DATA/BINARY lists must not contain Owner runtime/history/QA input files.
    import ast
    toc=ast.literal_eval((ROOT/'build'/'portable'/'CRM'/'Analysis-00.toc').read_text(encoding='utf-8'))
    forbidden=['/.runtime/','/conversation-assets/','/.qa/','04-crm-dev.md','04-crm-dev-compact.md','04-crm-review.md','04-crm-review-compact.md']
    def scan(value):
        if isinstance(value,(tuple,list)):
            if len(value)==3 and value[-1] in ['DATA','BINARY']:
                for item in value[:2]:
                    path=str(item).replace('\\','/').lower()
                    if any(bad in path for bad in forbidden):raise RuntimeError('Private file detected in build payload')
            for item in value:scan(item)
    scan(toc)
    release=inspect_release(output/'CRM.exe')
    (output/'release.json').write_text(json.dumps(release_metadata(release),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    archive=create_archive(release,ROOT/'portable',ROOT/'.qa'/'tmp')
    with archive, (output/'CRM-Windows-x64.zip').open('wb') as target:
        while chunk:=archive.read(1024*1024):target.write(chunk)
    print('PASS: executable and clean application ZIP built; private inputs excluded',flush=True)
    print('Executable bytes:',release.size,'SHA256:',release.sha256,flush=True)

if __name__=='__main__':main()
