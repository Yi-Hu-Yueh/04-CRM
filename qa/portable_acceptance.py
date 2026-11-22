"""Download via the button, then run the EXE outside the project with no Python PATH."""
import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright,expect

ROOT=Path(__file__).resolve().parent.parent
RUN=ROOT/'.qa'/('portable-'+uuid.uuid4().hex[:10]);RUN.mkdir(parents=True)
PYTHON=ROOT/'.venv/Scripts/python.exe'
password=secrets.token_urlsafe(24)
env=os.environ.copy();env.update(PYTHONIOENCODING='utf-8',TEMP=str(ROOT/'.qa/tmp'),TMP=str(ROOT/'.qa/tmp'))
source=RUN/'source';source.mkdir()
source_env={**env,'CRM_DB':str(source/'source.sqlite3'),'CRM_DATA_DIR':str(source/'runtime'),'CRM_SECRET_KEY':secrets.token_urlsafe(64),'CRM_QA_PASSWORD':password}
processes=[];handles=[];results=[]
def check(label):results.append(label);print('PASS: '+label,flush=True)
def run(command,environment):
    response=subprocess.run(command,cwd=RUN,env=environment,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if response.returncode:raise RuntimeError(response.stdout+response.stderr)
    return response.stdout
def spawn(command,environment,logname):
    log=(RUN/logname).open('a',encoding='utf-8');handles.append(log)
    process=subprocess.Popen(command,cwd=RUN,env=environment,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    processes.append(process);return process
def stop(process):
    if process.poll() is None:
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,check=False)
        process.wait(timeout=20)
def wait_url(url,process):
    for _ in range(100):
        if process.poll() is not None:raise RuntimeError('Test application exited; inspect isolated logs')
        try:
            with urlopen(url,timeout=1) as response:
                if response.status==200:return
        except Exception:time.sleep(.3)
    raise TimeoutError(url)
def login(page,url):
    page.goto(url)
    page.get_by_label('登入帳號',exact=True).fill('qa-owner')
    page.get_by_label('密碼',exact=True).fill(password)
    page.get_by_role('button',name='登入',exact=True).click()
    expect(page.get_by_role('heading',name='工作總覽.')).to_be_visible()
def download(page,url,target):
    page.goto(url)
    page.get_by_role('link',name='匯出獨立可執行系統',exact=True).click()
    expect(page.get_by_role('heading',name='匯出獨立可執行系統',exact=True)).to_be_visible()
    expect(page.locator('.notice')).to_contain_text('不含目前帳號')
    with page.expect_download(timeout=120000) as pending:page.get_by_role('button',name='下載獨立系統 ZIP',exact=True).click()
    pending.value.save_as(target)
    with zipfile.ZipFile(target) as archive:
        assert set(archive.namelist())=={'CRM/CRM.exe','CRM/release.json','CRM/使用說明.txt','CRM/THIRD-PARTY-NOTICES.txt'}
        metadata=json.loads(archive.read('CRM/release.json'))
        assert not metadata['contains_accounts'] and not metadata['contains_business_data']
        exe_hash=hashlib.sha256(archive.read('CRM/CRM.exe')).hexdigest()
        assert exe_hash==metadata['sha256']
    return exe_hash
try:
    run([str(PYTHON),str(ROOT/'manage.py'),'migrate','--noinput'],source_env)
    run([str(PYTHON),str(ROOT/'qa/fixture.py')],source_env)
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    source_url=f'http://127.0.0.1:{port}/'
    source_process=spawn([str(PYTHON),str(ROOT/'manage.py'),'runserver',f'127.0.0.1:{port}','--noreload'],source_env,'source.log')
    wait_url(source_url,source_process)
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        source_context=browser.new_context(viewport={'width':1440,'height':1000})
        page=source_context.new_page();login(page,source_url)
        page.get_by_role('link',name='匯出獨立可執行系統',exact=True).click()
        page.get_by_role('link',name='取消',exact=True).click()
        expect(page.get_by_role('heading',name='工作總覽.')).to_be_visible()
        check('visible export button, scope explanation and cancellation')
        original_hash=download(page,source_url,RUN/'download.zip')
        page.screenshot(path=str(RUN/'export-page.png'),full_page=True)
        check('real button downloads four-entry application-only ZIP with matching executable hash')
        colleague=browser.new_context();other=colleague.new_page()
        other.goto(source_url);other.get_by_label('登入帳號',exact=True).fill('qa-colleague');other.get_by_label('密碼',exact=True).fill(password);other.get_by_role('button',name='登入',exact=True).click()
        expect(other.get_by_role('link',name='匯出獨立可執行系統',exact=True)).to_have_count(0)
        assert other.goto(source_url+'system/export/').status==403
        check('ordinary employee cannot see or directly access system export')
        relocated=RUN/'獨立 測試系統';relocated.mkdir()
        with zipfile.ZipFile(RUN/'download.zip') as archive:archive.extractall(relocated)
        exe=relocated/'CRM/CRM.exe';data=relocated/'CRM/data'
        portable_env={k:v for k,v in env.items() if k not in ['VIRTUAL_ENV','PYTHONHOME','PYTHONPATH','DJANGO_SETTINGS_MODULE','CRM_DATA_DIR','CRM_SECRET_KEY','CRM_DB']}
        portable_env['PATH']=str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32')
        # Deliberately poison the inherited DB path; the copied EXE must ignore it.
        portable_env['CRM_DB']=source_env['CRM_DB']
        run([str(exe),'--data-dir',str(data),'--init-only'],portable_env)
        with sqlite3.connect(data/'crm.sqlite3') as connection:
            assert connection.execute('select count(*) from crm_employee').fetchone()[0]==0
            assert connection.execute('select count(*) from crm_company').fetchone()[0]==0
        assert (data/'secret.key').read_bytes()!=source_env['CRM_SECRET_KEY'].encode()
        check('relocated EXE initializes a fresh independent database/key with no Python in PATH')
        frozen_seed={**env,'CRM_DB':str(data/'crm.sqlite3'),'CRM_DATA_DIR':str(data),'CRM_QA_PASSWORD':password}
        run([str(PYTHON),str(ROOT/'qa/fixture.py')],frozen_seed)
        def start_exe(logname):
            process=spawn([str(exe),'--data-dir',str(data),'--port','0','--no-browser','--no-setup'],portable_env,logname)
            logpath=RUN/logname
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError(logpath.read_text(encoding='utf-8'))
                match=re.search(r'http://127\.0\.0\.1:\d+/',logpath.read_text(encoding='utf-8'))
                if match:
                    url=match.group(0);wait_url(url,process);return process,url
                time.sleep(.3)
            raise TimeoutError('Packaged application startup')
        frozen_process,frozen_url=start_exe('exe-first.log')
        frozen_context=browser.new_context(viewport={'width':1440,'height':1000});frozen_page=frozen_context.new_page();login(frozen_page,frozen_url)
        assert any(c['name'].startswith('crm_session_') for c in frozen_context.cookies())
        check('frozen application serves login, templates, static assets and individual authentication')
        manifest=json.loads((data/'fixture.json').read_text(encoding='utf-8'))
        detail=f'records/company/{manifest["company"]}/'
        frozen_page.goto(frozen_url+detail+'edit/')
        frozen_page.get_by_label('備註',exact=True).fill('獨立 EXE 儲存與重啟驗證')
        frozen_page.get_by_role('button',name='儲存',exact=True).click()
        expect(frozen_page.locator('main')).to_contain_text('獨立 EXE 儲存與重啟驗證')
        stop(frozen_process)
        frozen_process,restarted_url=start_exe('exe-restart.log')
        # Same data directory => same scoped cookie and persistent login/data.
        frozen_page.goto(restarted_url+detail)
        expect(frozen_page.locator('main')).to_contain_text('獨立 EXE 儲存與重啟驗證')
        check('actual EXE stop/start preserves saved records and its scoped login session')
        repeated_hash=download(frozen_page,restarted_url,RUN/'re-export.zip')
        assert repeated_hash==original_hash
        check('copied EXE re-exports itself through the same button without shipping its data')
        browser.close()
    (RUN/'result.json').write_text(json.dumps({'status':'PASS','checks':results,'executable_sha256':original_hash},ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT: '+str(RUN/'result.json'),flush=True)
finally:
    for process in reversed(processes):stop(process)
    for handle in handles:handle.close()
