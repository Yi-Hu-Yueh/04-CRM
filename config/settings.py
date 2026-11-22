import os
import secrets
import sys
import hashlib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RESOURCE_DIR = Path(getattr(sys,'_MEIPASS',BASE_DIR))
IS_FROZEN = bool(getattr(sys,'frozen',False))
RUNTIME = Path(os.environ.get('CRM_DATA_DIR',str(Path(sys.executable).parent/'data' if IS_FROZEN else BASE_DIR/'.runtime'))).resolve()
RUNTIME.mkdir(parents=True,exist_ok=True)
CRM_EXPORT_EXECUTABLE = Path(sys.executable) if IS_FROZEN else BASE_DIR/'dist'/'portable'/'CRM.exe'
CRM_EXPORT_METADATA = None if IS_FROZEN else BASE_DIR/'dist'/'portable'/'release.json'
CRM_PORTABLE_ASSETS = RESOURCE_DIR/'portable'
if not os.environ.get('CRM_SECRET_KEY'):
    key_file = RUNTIME / 'secret.key'
    try:
        with key_file.open('x', encoding='utf-8') as stream:
            stream.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    SECRET_KEY = key_file.read_text(encoding='utf-8')
else:
    SECRET_KEY = os.environ['CRM_SECRET_KEY']
DEBUG = os.environ.get('CRM_DEBUG', '0') == '1'
ALLOWED_HOSTS = os.environ.get('CRM_ALLOWED_HOSTS', '127.0.0.1,localhost,testserver').split(',')
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.staticfiles', 'crm']
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
]
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [RESOURCE_DIR / 'templates'], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth']}}]
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
    'NAME': os.environ.get('CRM_DB', str(RUNTIME / 'crm.sqlite3')),
    'OPTIONS': {'transaction_mode': 'IMMEDIATE', 'timeout': 10},
    'TEST': {'NAME': os.environ.get('CRM_TEST_DB', str(BASE_DIR / '.qa' / 'test.sqlite3'))}}}
if Path(DATABASES['default']['NAME']).resolve() == Path(DATABASES['default']['TEST']['NAME']).resolve():
    raise RuntimeError('測試資料庫不可與業務資料庫使用相同路徑。')
AUTH_USER_MODEL = 'crm.Employee'
AUTHENTICATION_BACKENDS = ['crm.auth.EmployeeBackend']
LANGUAGE_CODE = 'zh-hant'
TIME_ZONE = 'Asia/Taipei'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATICFILES_DIRS = [RESOURCE_DIR / 'static']
LOGIN_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = os.environ.get('CRM_HTTPS') == '1'
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE
if IS_FROZEN:
    cookie_scope=hashlib.sha256(str(RUNTIME).encode()).hexdigest()[:12]
    SESSION_COOKIE_NAME='crm_session_'+cookie_scope
    CSRF_COOKIE_NAME='crm_csrf_'+cookie_scope
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
