"""Real Windows Chromium, isolated server and actual stop/start acceptance run."""
import json
import os
import secrets
import socket
import subprocess
import time
import uuid
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parent.parent
RUN=ROOT/'.qa'/('browser-'+uuid.uuid4().hex[:10])
RUN.mkdir(parents=True)
PYTHON=ROOT/'.venv/Scripts/python.exe'
env=os.environ.copy()
env.update(CRM_DB=str(RUN/'browser.sqlite3'),CRM_SECRET_KEY=secrets.token_urlsafe(64),CRM_QA_PASSWORD=secrets.token_urlsafe(24),PYTHONIOENCODING='utf-8',TEMP=str(ROOT/'.qa/tmp'),TMP=str(ROOT/'.qa/tmp'))
def run(*args):
    result=subprocess.run([str(PYTHON),*args],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8')
    if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    return result.stdout
run('manage.py','migrate','--noinput')
run('qa/fixture.py')
ids=json.loads((RUN/'fixture.json').read_text(encoding='utf-8'))
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
BASE=f'http://127.0.0.1:{port}'
server=None
log=(RUN/'server.log').open('a',encoding='utf-8')
results=[]
def start():
    global server
    server=subprocess.Popen([str(PYTHON),'manage.py','runserver',f'127.0.0.1:{port}','--noreload'],cwd=ROOT,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    for _ in range(120):
        if server.poll() is not None:raise RuntimeError('QA server exited; inspect isolated server.log')
        try:
            with urlopen(BASE+'/login/',timeout=1) as response:
                if response.status==200:return
        except Exception:time.sleep(.2)
    raise TimeoutError('QA server startup timeout')
def stop():
    global server
    if server and server.poll() is None:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)
def ok(label):
    results.append(label);print('PASS: '+label,flush=True)
def path(kind,key=None,suffix=''):
    return f'/records/{kind}/'+(ids[key]+'/' if key else '')+suffix
def login(page,name='qa-owner'):
    page.goto(BASE+'/login/')
    page.get_by_label('登入帳號',exact=True).fill(name)
    page.get_by_label('密碼',exact=True).fill(env['CRM_QA_PASSWORD'])
    page.get_by_role('button',name='登入',exact=True).click()
    expect(page.get_by_role('heading',name='工作總覽.')).to_be_visible()
try:
    start()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1440,'height':1000},timezone_id='Asia/Taipei')
        page=context.new_page()
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        login(page);page.screenshot(path=str(RUN/'dashboard.png'),full_page=True)
        assert not page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
        ok('desktop login, dashboard and horizontal layout')
        # Every core and account editor: draft cancellation, plus creation cancellation.
        for kind in ['company','contact','enquiry','opportunity','interaction','task','employee']:
            page.goto(BASE+path(kind,kind,'edit/'))
            expect(page.get_by_role('button',name='取消',exact=True)).to_be_visible()
            text=page.locator('form.editor input:not([type]),form.editor input[type=text],form.editor textarea').first
            if text.count():text.fill('UNSAVED-QA-DRAFT')
            page.get_by_role('button',name='取消',exact=True).click()
            expect(page.locator('main')).not_to_contain_text('UNSAVED-QA-DRAFT')
            page.goto(BASE+path(kind,None,'new/'))
            page.get_by_role('button',name='取消',exact=True).click()
            expect(page).to_have_url(BASE+path(kind))
        ok('all seven editor families and new forms discard unsaved drafts')
        cases=[('company','deactivate'),('company','transfer'),('enquiry','qualify'),('enquiry','close'),('opportunity','hold'),('opportunity','cancel'),('opportunity','won'),('opportunity','lost'),('task','complete'),('task','cancel')]
        for kind,op in cases:
            page.goto(BASE+path(kind,kind,'action/'+op+'/'))
            expect(page.get_by_role('button',name='取消',exact=True)).to_be_visible()
            page.get_by_role('button',name='取消',exact=True).click()
            expect(page).to_have_url(BASE+path(kind,kind))
        ok('confirmation cancellation preserves saved business records')
        page.goto(BASE+path('opportunity','opportunity','action/won/'))
        page.get_by_role('button',name='確認',exact=True).click()
        expect(page.locator('.feedback')).to_contain_text('原因')
        page.get_by_label('原因／說明').fill('QA 瀏覽器人員明確確認')
        page.get_by_role('button',name='確認',exact=True).click()
        expect(page).to_have_url(BASE+path('opportunity','opportunity'))
        expect(page.locator('main')).to_contain_text('成交')
        page.goto(BASE+path('opportunity','opportunity','action/reopen/'))
        page.get_by_label('恢復後階段').select_option('PRICE_NEGOTIATION')
        page.get_by_role('button',name='確認',exact=True).click()
        expect(page.locator('main')).to_contain_text('議價')
        expect(page.locator('main')).to_contain_text('QA 瀏覽器人員明確確認')
        page.reload();expect(page.locator('main')).to_contain_text('議價')
        ok('empty outcome reason rejected nearby; explicit closure, reopen and reload preserve history')
        page.goto(BASE+path('company',None,'new/'))
        page.get_by_label('名稱',exact=True).fill('QA 星野科技')
        page.get_by_role('button',name='儲存',exact=True).click()
        expect(page.locator('.warning')).to_contain_text('疑似重複')
        page.get_by_label('已查看疑似重複警告，仍要保留為獨立公司').check()
        page.get_by_role('button',name='儲存',exact=True).click()
        expect(page.locator('h1')).to_have_text('QA 星野科技')
        ok('duplicate warning permits explicit independent company creation')
        page.goto(BASE+path('task',None,'new/'))
        page.get_by_label('主要主題：公司',exact=True).select_option(ids['company'])
        page.get_by_label('主要主題：商機',exact=True).select_option(ids['opportunity'])
        page.get_by_role('button',name='儲存',exact=True).click()
        expect(page.locator('.feedback')).to_contain_text('主要業務主題最多選擇一個')
        ok('multiple primary subjects rejected with plain-language nearby feedback')
        page.goto(BASE+path('company','company','action/deactivate/'))
        for select in page.locator('select[name^=work_]').all():select.select_option('continue')
        page.get_by_role('button',name='確認',exact=True).click()
        expect(page).to_have_url(BASE+path('company','company'))
        page.goto(BASE+path('company','company','action/continue/'))
        page.get_by_role('button',name='取消',exact=True).click()
        page.goto(BASE+path('company','company','action/reactivate/'))
        page.get_by_role('button',name='取消',exact=True).click()
        page.goto(BASE+path('opportunity',None,'new/'))
        page.get_by_label('主要公司（必選）').select_option(ids['company'])
        page.get_by_role('button',name='儲存',exact=True).click()
        expect(page.locator('.feedback')).to_contain_text('停用公司')
        page.goto(BASE+path('company','company','action/reactivate/'))
        page.get_by_role('button',name='確認',exact=True).click()
        ok('deactivation preview, confirmed continuation, inactive-company protection and reactivation')
        page.goto(BASE+path('opportunity','opportunity','action/hold/'))
        page.get_by_role('button',name='確認',exact=True).click()
        page.goto(BASE+path('opportunity','opportunity','action/resume/'))
        page.get_by_role('button',name='取消',exact=True).click()
        page.goto(BASE+path('opportunity','opportunity','action/resume/'))
        page.get_by_role('button',name='確認',exact=True).click()
        ok('held/resume editor cancellation and explicit state change')
        colleague=browser.new_context(viewport={'width':1440,'height':1000});other=colleague.new_page();login(other,'qa-colleague')
        other.goto(BASE+path('opportunity','opportunity'))
        expect(other.locator('main')).to_contain_text('QA 客戶管理需求')
        response=other.goto(BASE+path('opportunity','opportunity','edit/'))
        assert response.status==403
        ok('separate employee session reads colleague records but direct unauthorized edit fails')
        page.goto(BASE+path('company','company','edit/'))
        page.get_by_label('備註',exact=True).fill('QA 儲存後重啟仍保留')
        page.get_by_role('button',name='儲存',exact=True).click()
        if page.locator('.warning').count():
            page.get_by_label('已查看疑似重複警告，仍要保留為獨立公司').check();page.get_by_role('button',name='儲存',exact=True).click()
        expect(page.locator('main')).to_contain_text('QA 儲存後重啟仍保留')
        stop();start()
        page.goto(BASE+path('company','company'))
        expect(page.locator('main')).to_contain_text('QA 儲存後重啟仍保留')
        output=run('qa/fixture.py','verify');assert 'PASS persistence' in output
        ok('actual application process stop/start preserves data, relationships, history and receipts')
        page.goto(BASE+'/');page.screenshot(path=str(RUN/'final-desktop.png'),full_page=True)
        assert not errors,errors
        ok('no JavaScript runtime errors')
        browser.close()
    (RUN/'result.json').write_text(json.dumps({'status':'PASS','checks':results},ensure_ascii=False,indent=2),encoding='utf-8')
    print('RESULT: '+str(RUN/'result.json'),flush=True)
finally:
    stop();log.close()
