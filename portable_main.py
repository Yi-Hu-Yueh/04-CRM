"""Frozen entry point. No external Python or development server required."""
import argparse
import os
import sys
import socket
import webbrowser
from pathlib import Path

def initialize_database():
    from django.core.management import call_command
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor=MigrationExecutor(connection)
    plan=executor.migration_plan(executor.loader.graph.leaf_nodes())
    if not plan:return
    # Do not automatically migrate an existing business database to a new version.
    tables=set(connection.introspection.table_names())
    business={'crm_employee','crm_company','crm_contact','crm_enquiry','crm_opportunity','crm_task','crm_interaction','crm_operationevent'}
    with connection.cursor() as cursor:
        for table in sorted(tables & business):
            cursor.execute(f'SELECT EXISTS(SELECT 1 FROM "{table}")')
            if cursor.fetchone()[0]:
                raise RuntimeError('既有資料需要版本升級；原資料已保留，請先備份並由管理者處理。')
    call_command('migrate',interactive=False,verbosity=0)

def bind_listener(port):
    listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        listener.bind(('127.0.0.1',port))
        return listener
    except Exception:
        listener.close();raise

def create_local_server(application, requested_port=None):
    from waitress import create_server
    ports=[requested_port] if requested_port is not None else [18040,0]
    for index,port in enumerate(ports):
        try:listener=bind_listener(port)
        except OSError:
            if index==len(ports)-1:raise
            continue
        try:return create_server(application,sockets=[listener])
        except Exception:
            listener.close();raise

def main():
    parser=argparse.ArgumentParser(description='CRM 獨立系統')
    parser.add_argument('--data-dir',type=Path,help='指定此系統自己的資料目錄')
    parser.add_argument('--port',type=int,help='指定本機埠；0 表示自動選擇')
    parser.add_argument('--no-browser',action='store_true',help='不自動開啟瀏覽器')
    parser.add_argument('--no-setup',action='store_true',help='略過互動初始化；須已有員工帳號')
    parser.add_argument('--init-only',action='store_true',help='只初始化空白資料結構，不啟動伺服器')
    args=parser.parse_args()
    # A copied program always owns its own data/settings; do not inherit source DB credentials.
    for name in ['CRM_DB','CRM_SECRET_KEY','DJANGO_SETTINGS_MODULE']:
        os.environ.pop(name,None)
    if args.data_dir:os.environ['CRM_DATA_DIR']=str(args.data_dir.resolve())
    os.environ['DJANGO_SETTINGS_MODULE']='config.settings'
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from crm.models import Employee
    from crm.release import APP_VERSION
    initialize_database()
    print(f'CRM 獨立系統 {APP_VERSION}',flush=True)
    if args.init_only:
        print('空白資料結構已備妥。',flush=True);return 0
    if not Employee.objects.exists():
        if args.no_setup:raise RuntimeError('尚無員工帳號，請正常啟動系統以設定個人帳密。')
        call_command('bootstrap')
    server=create_local_server(get_wsgi_application(),args.port)
    url=f'http://127.0.0.1:{server.effective_port}/'
    print(f'本機網址：{url}',flush=True)
    print(f'資料目錄：{settings.RUNTIME}',flush=True)
    print('保留此視窗；按 Ctrl+C 停止系統。',flush=True)
    if not args.no_browser:webbrowser.open(url)
    try:server.run()
    except KeyboardInterrupt:pass
    finally:server.close()
    return 0

if __name__=='__main__':
    for stream in [sys.stdout,sys.stderr]:
        if stream and hasattr(stream,'reconfigure'):
            stream.reconfigure(encoding='utf-8',errors='backslashreplace')
    import multiprocessing
    multiprocessing.freeze_support()
    try:raise SystemExit(main())
    except Exception as exc:
        print(f'無法啟動 CRM：{exc}',file=sys.stderr,flush=True)
        if sys.stdin and sys.stdin.isatty():input('按 Enter 關閉。')
        raise SystemExit(1)
