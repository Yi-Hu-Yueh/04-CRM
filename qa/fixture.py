"""Disposable browser/persistence fixture. Never writes to the Owner database."""
import os
import sys
import json
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
assert ROOT/ '.qa' in Path(os.environ['CRM_DB']).resolve().parents
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from django.utils import timezone
from crm import models as m, services as s

if len(sys.argv)>1 and sys.argv[1]=='verify':
    manifest=json.loads((Path(os.environ['CRM_DB']).parent/'fixture.json').read_text(encoding='utf-8'))
    for kind,key in [('company','company'),('contact','contact'),('enquiry','enquiry'),('opportunity','opportunity'),('task','task'),('interaction','interaction')]:
        assert s.MODELS[kind].objects.filter(pk=manifest[key]).exists(),kind
    contact=m.Contact.objects.get(pk=manifest['contact'])
    assert contact.affiliations.count()==2 and contact.affiliations.filter(ended_at=None).count()==1
    opp=m.Opportunity.objects.get(pk=manifest['opportunity'])
    assert opp.participations.count()==2
    assert m.OperationEvent.objects.filter(target_id=opp.pk,operation='won').exists()
    assert m.OperationEvent.objects.filter(target_id=opp.pk,operation='reopen').exists()
    original=m.Interaction.objects.get(pk=manifest['interaction'])
    assert original.context_snapshot['company_id']==manifest['company']
    assert m.CommandReceipt.objects.exists()
    assert m.ContinuationGrant.objects.filter(company_id=manifest['company']).exists()
    print('PASS persistence: six core entities, affiliation/participation history, closure/reopen evidence, context and receipts')
    raise SystemExit()

assert not m.Employee.objects.exists(),'Fixture must use a fresh disposable database'
employee=m.Employee.objects.create_user('qa-owner',os.environ['CRM_QA_PASSWORD'],display_name='QA 驗證員')
other=m.Employee.objects.create_user('qa-colleague',os.environ['CRM_QA_PASSWORD'],display_name='QA 同事')
for cap,_ in m.EmployeeGrant.CAPABILITIES:m.EmployeeGrant.objects.create(employee=employee,capability=cap,granted_by=employee)
def create(kind,**data):return s.execute(employee,uuid.uuid4(),'create',kind,data=data)
def command(obj,op,**data):
    obj.refresh_from_db()
    return s.execute(employee,uuid.uuid4(),op,s.kind_of(obj),obj.pk,obj.version,data)
company=create('company',name='QA 星野科技',phone='QA-001',notes='隔離驗證用，非正式客戶資料。')
second=create('company',name='QA 森川設計')
person=create('contact',name='QA 陳小姐',company_affiliation=company)
person2=create('contact',name='QA 林先生')
opp=create('opportunity',company=company,title='QA 客戶管理需求',participants=[person,person2])
enquiry=create('enquiry',subject='QA 導入詢問',company=company,opportunity=opp)
interaction=create('interaction',content='QA 需求訪談已實際進行',fact_confirmed=True,occurred_at=timezone.now(),subject_opportunity=opp)
task=create('task',title='QA 整理訪談資料',due_on=timezone.localdate(),subject_opportunity=opp)
command(person,'edit',company_affiliation=second)
command(opp,'won',reason='QA 確認成交的歷史原因')
command(opp,'reopen',stage='SOLUTION_DISCUSSION')
manifest={key:str(obj.pk) for key,obj in [('company',company),('contact',person),('enquiry',enquiry),('opportunity',opp),('interaction',interaction),('task',task),('employee',employee),('other',other)]}
(Path(os.environ['CRM_DB']).parent/'fixture.json').write_text(json.dumps(manifest),encoding='utf-8')
print('PASS isolated fixture created')
