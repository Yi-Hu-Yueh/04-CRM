import hashlib
import io
import json
import tempfile
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch
from django.conf import settings
from django.test import TransactionTestCase,override_settings,Client
from django.urls import reverse
from crm import models as m, services as s
from crm.packaging import inspect_release,create_archive,PackageError
from crm.release import APP_VERSION
from crm.tests.test_phase1 import FIXTURE_PASSWORD

class SystemExportTests(TransactionTestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(dir=Path(settings.BASE_DIR)/'.qa')
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        self.exe=self.root/'CRM.exe';self.exe.write_bytes(b'MZ-TEST-EXECUTABLE')
        self.metadata=self.root/'release.json'
        self.metadata.write_text(json.dumps({'version':APP_VERSION,'sha256':hashlib.sha256(self.exe.read_bytes()).hexdigest()}),encoding='utf-8')
        self.assets=self.root/'assets';self.assets.mkdir()
        (self.assets/'使用說明.txt').write_text('解壓縮後執行 CRM.exe。',encoding='utf-8')
        (self.assets/'THIRD-PARTY-NOTICES.txt').write_text('Test notices',encoding='utf-8')
        for name in ['secret.key','crm.sqlite3','04-CRM-DEV.md']:(self.root/name).write_text('OWNER_PRIVATE_DATA',encoding='utf-8')
        self.admin=m.Employee.objects.create(login_name='export-admin',password=FIXTURE_PASSWORD)
        self.other=m.Employee.objects.create(login_name='export-reader',password=FIXTURE_PASSWORD)
        m.EmployeeGrant.objects.create(employee=self.admin,capability='ACCOUNT_ADMIN',granted_by=self.admin)
        self.options=override_settings(CRM_EXPORT_EXECUTABLE=self.exe,CRM_EXPORT_METADATA=self.metadata,CRM_PORTABLE_ASSETS=self.assets,RUNTIME=self.root/'runtime')
        self.options.enable();self.addCleanup(self.options.disable)
        self.client.force_login(self.admin)

    def test_package_allowlist_and_no_owner_data(self):
        release=inspect_release(self.exe,self.metadata)
        with create_archive(release,self.assets,self.root/'tmp') as stream,zipfile.ZipFile(stream) as archive:
            self.assertEqual(set(archive.namelist()),{'CRM/CRM.exe','CRM/release.json','CRM/使用說明.txt','CRM/THIRD-PARTY-NOTICES.txt'})
            self.assertEqual(archive.read('CRM/CRM.exe'),self.exe.read_bytes())
            self.assertFalse(json.loads(archive.read('CRM/release.json'))['contains_business_data'])
            self.assertNotIn(b'OWNER_PRIVATE_DATA',b''.join(archive.read(n) for n in archive.namelist()))

    def test_button_confirmation_cancel_and_readonly_get(self):
        self.assertContains(self.client.get('/'),'匯出獨立可執行系統')
        before=m.OperationEvent.objects.count()
        response=self.client.get(reverse('export_system'))
        self.assertContains(response,'不含目前帳號')
        self.assertContains(response,'取消')
        self.assertEqual(m.OperationEvent.objects.count(),before)

    def test_download_and_duplicate_command_has_single_attributable_event(self):
        command=uuid.uuid4()
        for _ in range(2):
            response=self.client.post(reverse('export_system'),{'command_id':command})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response['Content-Type'],'application/zip')
            payload=b''.join(response.streaming_content);response.close()
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:self.assertIn('CRM/CRM.exe',archive.namelist())
        events=m.OperationEvent.objects.filter(operation='export_system')
        self.assertEqual(events.count(),1);self.assertEqual(events.get().actor,self.admin)
        self.assertFalse(events.get().after['contains_business_data'])

    def test_missing_and_tampered_executable_fail_near_controls_without_audit(self):
        self.exe.write_bytes(b'MZ-changed')
        response=self.client.post(reverse('export_system'),{'command_id':uuid.uuid4()})
        self.assertContains(response,'完整性檢查未通過',status_code=503)
        self.assertContains(response,'role="alert"',status_code=503)
        self.assertFalse(m.OperationEvent.objects.filter(operation='export_system').exists())
        self.exe.unlink()
        response=self.client.post(reverse('export_system'),{'command_id':uuid.uuid4()})
        self.assertContains(response,'尚未備妥',status_code=503)

    def test_reader_global_editor_and_anonymous_cannot_export(self):
        self.client.force_login(self.other)
        m.EmployeeGrant.objects.create(employee=self.other,capability='GLOBAL_EDIT',granted_by=self.admin)
        self.assertNotContains(self.client.get('/'),'匯出獨立可執行系統')
        with patch('crm.packaging.create_archive') as build:
            self.assertEqual(self.client.get(reverse('export_system')).status_code,403)
            self.assertEqual(self.client.post(reverse('export_system'),{'command_id':uuid.uuid4()}).status_code,403)
            build.assert_not_called()
        self.client.logout();self.assertEqual(self.client.get(reverse('export_system')).status_code,302)

    def test_csrf_unknown_fields_and_permission_recheck(self):
        secure=Client(enforce_csrf_checks=True);secure.force_login(self.admin)
        self.assertEqual(secure.post(reverse('export_system'),{'command_id':uuid.uuid4()}).status_code,403)
        response=self.client.post(reverse('export_system'),{'command_id':uuid.uuid4(),'path':str(self.root/'secret.key')})
        self.assertEqual(response.status_code,422)
        from crm import packaging
        actual=packaging.create_archive
        def revoke(*args,**kwargs):
            archive=actual(*args,**kwargs)
            m.EmployeeGrant.objects.filter(employee=self.admin,capability='ACCOUNT_ADMIN').delete()
            return archive
        with patch('crm.packaging.create_archive',side_effect=revoke):
            response=self.client.post(reverse('export_system'),{'command_id':uuid.uuid4()})
        self.assertEqual(response.status_code,403)
        self.assertFalse(m.OperationEvent.objects.filter(operation='export_system').exists())

    def test_changed_file_during_download_build_is_rejected(self):
        release=inspect_release(self.exe,self.metadata)
        self.exe.write_bytes(b'MZ-new')
        with self.assertRaises(PackageError):create_archive(release,self.assets,self.root/'tmp')

    def test_export_does_not_modify_existing_business_records(self):
        company=s.execute(self.admin,uuid.uuid4(),'create','company',data={'name':'Owner 公司'})
        before=s.snapshot(company)
        response=self.client.post(reverse('export_system'),{'command_id':uuid.uuid4()})
        self.assertEqual(response.status_code,200)
        b''.join(response.streaming_content);response.close()
        company.refresh_from_db();self.assertEqual(s.snapshot(company),before)

    def test_local_server_falls_back_without_reusing_another_application(self):
        from portable_main import create_local_server
        expected=object()
        listener=object()
        with patch('portable_main.bind_listener',side_effect=[OSError('port occupied'),listener]) as bind,patch('waitress.create_server',return_value=expected) as create:
            self.assertIs(create_local_server(object()),expected)
        self.assertEqual([c.args[0] for c in bind.call_args_list],[18040,0])
        self.assertEqual(create.call_args.kwargs['sockets'],[listener])
