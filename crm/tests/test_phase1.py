"""Isolated functional and transaction acceptance checks for Phase 1."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timezone as dt_timezone
from threading import Barrier
from unittest.mock import patch
from io import StringIO
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, TransactionTestCase, Client
from django.db import transaction, IntegrityError, close_old_connections
from django.urls import reverse
from django.contrib.auth.hashers import make_password
from crm import services as s, models as m
from crm.views import statistics

FIXTURE_PASSWORD = make_password('test-isolated-password')

class Helpers:
    def setup_people(self):
        self.a=m.Employee.objects.create(login_name='alice',password=FIXTURE_PASSWORD)
        self.b=m.Employee.objects.create(login_name='bob',password=FIXTURE_PASSWORD)
        self.c=m.Employee.objects.create(login_name='carol',password=FIXTURE_PASSWORD)
    def grant(self,actor,*names):
        for name in names:m.EmployeeGrant.objects.get_or_create(employee=actor,capability=name,defaults={'granted_by':self.a})
    def create(self,kind,actor=None,**data):
        return s.execute(actor or self.a,uuid.uuid4(),'create',kind,data=data)
    def command(self,obj,op='edit',actor=None,**data):
        obj.refresh_from_db()
        return s.execute(actor or self.a,uuid.uuid4(),op,s.kind_of(obj),obj.pk,obj.version,data)
    def preview_command(self,obj,op,actor=None,decide='continue',**data):
        actor=actor or self.a
        obj.refresh_from_db()
        token,rows=s.preview(actor,s.kind_of(obj),obj.pk,op)
        decisions={f'{k}:{v.pk}':('transfer' if op=='transfer' else decide) for k,v in rows if op=='transfer' or k in ['enquiry','opportunity','task']}
        return self.command(obj,op,actor,preview=token,decisions=decisions,**data)
    def forbidden(self,fn,code):
        with self.assertRaises(s.BusinessError) as error:fn()
        self.assertEqual(error.exception.code,code)

class Phase1Tests(Helpers,TestCase):
    def setUp(self):self.setup_people()

    def test_AT01_AT02_company_contact_identity_and_nonunique_warning(self):
        company=self.create('company',name='同名公司',phone='123')
        other=self.create('company',name='同名公司',phone='123')
        contact=self.create('contact',name='窗口',company_affiliation=company)
        self.assertNotEqual(company.pk,other.pk)
        self.assertEqual(len(s.duplicates({'name':'同名公司'})),2)
        self.assertEqual(s.duplicates({'name':''}),[])
        self.assertEqual(contact.affiliations.get(ended_at=None).company,company)

    def test_AT03_affiliation_and_participation_history(self):
        first=self.create('company',name='甲');second=self.create('company',name='乙')
        contact=self.create('contact',company_affiliation=first)
        opportunity=self.create('opportunity',company=first,participants=[contact])
        interaction=self.create('interaction',fact_confirmed=True,subject_opportunity=opportunity)
        old_context=interaction.context_snapshot
        self.command(contact,company_affiliation=second)
        self.assertEqual(contact.affiliations.filter(ended_at=None).get().company,second)
        self.assertEqual(contact.affiliations.count(),2)
        self.assertEqual(opportunity.participations.filter(removed_event=None).count(),1)
        interaction.refresh_from_db();self.assertEqual(interaction.context_snapshot,old_context)
        self.command(contact,company_affiliation=None)
        self.assertFalse(contact.affiliations.filter(ended_at=None).exists())

    def test_AT04_four_optional_links_and_retries(self):
        company=self.create('company');other=self.create('company')
        opportunity=self.create('opportunity',company=company)
        for data in [{},{'company':company},{'opportunity':opportunity},{'company':company,'opportunity':opportunity}]:
            enquiry=self.create('enquiry',**data)
            if 'company' not in data:self.assertIsNone(enquiry.company_id)
            self.command(enquiry,'close')
        self.forbidden(lambda:self.create('enquiry',company=other,opportunity=opportunity),'VALIDATION_ERROR')
        cmd=uuid.uuid4();data={'opportunity':opportunity}
        first=s.execute(self.a,cmd,'create','enquiry',data=data)
        second=s.execute(self.a,cmd,'create','enquiry',data=data)
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(m.Opportunity.objects.count(),1)
        self.forbidden(lambda:s.execute(self.a,cmd,'create','enquiry',data={}), 'COMMAND_KEY_REUSED')
        linked=self.create('enquiry',company=company,opportunity=opportunity)
        self.forbidden(lambda:self.command(opportunity,company=other),'VALIDATION_ERROR')

    def test_AT03_interaction_correction_preserves_original_company_context(self):
        first=self.create('company',name='原公司');second=self.create('company',name='另一公司')
        opp=self.create('opportunity',company=first)
        interaction=self.create('interaction',fact_confirmed=True,subject_opportunity=opp,content='原始內容')
        original=interaction.context_snapshot
        self.command(opp,company=second)
        corrected=self.command(interaction,content='更正內容')
        self.assertEqual(corrected.context_snapshot,original)
        explicit=self.command(interaction,subject_opportunity=None,subject_company=second)
        self.assertEqual(explicit.context_snapshot['company_id'],str(second.pk))
        evidence=m.OperationEvent.objects.filter(target_id=interaction.pk,operation='edit').last()
        self.assertEqual(evidence.before['context_snapshot'],original)

    def test_AT05_AT06_participants_stages_and_backward_movement(self):
        company=self.create('company');other=self.create('company')
        people=[self.create('contact',company_affiliation=other),self.create('contact')]
        opp=self.create('opportunity',company=company,participants=people)
        self.create('opportunity',company=company)
        for stage in ['SOLUTION_DISCUSSION','PRICE_NEGOTIATION','NEEDS_CONFIRMATION']:
            opp=self.command(opp,stage=stage)
            self.assertEqual(opp.stage,stage)
        self.assertEqual(opp.participations.filter(removed_event=None).count(),2)
        self.forbidden(lambda:self.command(opp,stage='UNAPPROVED'),'VALIDATION_ERROR')
        self.command(opp,participants=[])
        self.assertEqual(opp.participations.count(),2)
        self.assertEqual(opp.participations.filter(removed_event=None).count(),0)

    def test_AT07_human_outcomes_and_same_id_reopening(self):
        opp=self.create('opportunity',company=self.create('company'))
        for op in ['won','lost']:
            self.forbidden(lambda:self.command(opp,op,reason='  '),'VALIDATION_ERROR')
            result=self.command(opp,op,reason='人員確認原因')
            self.assertEqual(result.current_outcome_by,self.a)
            old_id=opp.pk
            self.command(opp,'reopen',stage='PRICE_NEGOTIATION')
            opp.refresh_from_db()
            self.assertEqual(opp.pk,old_id);self.assertEqual(opp.status,'ACTIVE')
            self.assertTrue(m.OperationEvent.objects.filter(target_id=opp.pk,reason='人員確認原因').exists())
        self.command(opp,'hold');opp.refresh_from_db();self.assertEqual(opp.status,'HELD')
        self.command(opp,'cancel');opp.refresh_from_db();self.assertEqual(opp.status,'CANCELLED')
        self.assertEqual(statistics()['opportunity_states'][-1][1],[])

    def test_AT08_AT09_plan_fact_and_optional_dates(self):
        task=self.create('task')
        self.command(task,'complete')
        self.assertEqual(m.Interaction.objects.count(),0)
        interaction=self.create('interaction',fact_confirmed=True)
        self.assertIsNone(interaction.occurred_at)
        self.assertIsNone(task.due_on)
        self.assertEqual(statistics()['due'],0)
        company=self.create('company');enquiry=self.create('enquiry')
        for kind in ['task','interaction']:
            self.forbidden(lambda:self.create(kind,subject_company=company,subject_enquiry=enquiry,fact_confirmed=True) if kind=='interaction' else self.create(kind,subject_company=company,subject_enquiry=enquiry),'VALIDATION_ERROR')
        self.forbidden(lambda:self.create('interaction',fact_confirmed=False),'VALIDATION_ERROR')

    def test_AT10_AT12_deactivation_freeze_continuation_generation(self):
        company=self.create('company');opp=self.create('opportunity',company=company)
        self.preview_command(company,'deactivate',decide='freeze')
        self.forbidden(lambda:self.command(opp,title='未確認續辦'),'CONTINUATION_REQUIRED')
        self.forbidden(lambda:self.create('opportunity',company=company),'COMPANY_INACTIVE')
        self.grant(self.b,'GLOBAL_EDIT')
        self.forbidden(lambda:self.create('opportunity',actor=self.b,company=company),'COMPANY_INACTIVE')
        self.preview_command(company,'continue')
        self.command(opp,title='已明確續辦')
        grant=m.ContinuationGrant.objects.get(opportunity=opp)
        self.create('task',actor=self.b,subject_opportunity=opp,continuation_grant=grant)
        self.create('interaction',actor=self.b,fact_confirmed=True,subject_opportunity=opp,continuation_grant=grant)
        company.refresh_from_db();self.assertFalse(company.is_active)
        self.forbidden(lambda:self.preview_command(company,'deactivate'),'VERSION_CONFLICT')
        self.command(company,'reactivate')
        self.create('opportunity',company=company)
        self.preview_command(company,'deactivate',decide='freeze')
        self.forbidden(lambda:self.command(opp,title='舊世代失效'),'CONTINUATION_REQUIRED')
        self.assertTrue(m.ContinuationGrant.objects.filter(pk=grant.pk).exists())

    def test_AT12_cannot_clear_subject_or_use_unrelated_grant(self):
        company=self.create('company');opp=self.create('opportunity',company=company)
        another=self.create('opportunity',company=company)
        task=self.create('task',subject_opportunity=opp)
        self.preview_command(company,'deactivate',decide='freeze')
        self.forbidden(lambda:self.command(task,subject_opportunity=None),'CONTINUATION_REQUIRED')
        company.refresh_from_db();token,rows=s.preview(self.a,'company',company.pk,'continue')
        decisions={f'{k}:{v.pk}':('continue' if v.pk==opp.pk else 'freeze') for k,v in rows if k in ['task','opportunity','enquiry']}
        self.command(company,'continue',preview=token,decisions=decisions)
        grant=m.ContinuationGrant.objects.get(opportunity=opp)
        self.forbidden(lambda:self.create('task',subject_opportunity=another,continuation_grant=grant),'CONTINUATION_REQUIRED')
        intake=self.create('enquiry',company=company)
        self.assertEqual(intake.status,'OPEN')

    def test_AT11_AT14_transfer_and_creator_or_owner(self):
        obj=self.create('company')
        self.forbidden(lambda:self.preview_command(obj,'transfer',new_owner=self.b.pk),'PERMISSION_DENIED')
        self.grant(self.a,'GLOBAL_EDIT')
        self.forbidden(lambda:self.preview_command(obj,'transfer',new_owner=self.b.pk),'PERMISSION_DENIED')
        self.grant(self.c,'TRANSFER')
        self.preview_command(obj,'transfer',actor=self.c,new_owner=str(self.b.pk))
        self.command(obj,actor=self.a,name='建立者仍可修改')
        self.command(obj,actor=self.b,name='目前負責人可修改')
        self.forbidden(lambda:self.command(obj,actor=self.c,name='僅轉交權不能編輯'),'PERMISSION_DENIED')
        self.preview_command(obj,'transfer',actor=self.c,new_owner=str(self.c.pk))
        self.forbidden(lambda:self.command(obj,actor=self.b,name='原負責人不能修改'),'PERMISSION_DENIED')
        obj.refresh_from_db();self.assertEqual(obj.created_by,self.a)
        self.forbidden(lambda:self.command(obj,created_by_id=self.b.pk),'VALIDATION_ERROR')
        self.forbidden(lambda:self.command(obj,owner=self.b.pk),'VALIDATION_ERROR')

    def test_AT11_transfer_only_selected_work(self):
        company=self.create('company');opp=self.create('opportunity',company=company)
        self.grant(self.c,'TRANSFER')
        token,rows=s.preview(self.c,'company',company.pk,'transfer')
        self.command(company,'transfer',actor=self.c,preview=token,decisions={f'company:{company.pk}':'transfer'},new_owner=str(self.b.pk))
        company.refresh_from_db();opp.refresh_from_db()
        self.assertEqual(company.owner,self.b);self.assertEqual(opp.owner,self.a)

    def test_AT13_AT14_accounts_special_authorization_and_hmac(self):
        self.forbidden(lambda:self.create('employee',login_name='new',password='private'),'PERMISSION_DENIED')
        self.grant(self.a,'ACCOUNT_ADMIN')
        user=self.create('employee',login_name=' NEW ',password='private',capabilities=['TRANSFER'])
        self.assertTrue(user.check_password('private'));self.assertEqual(user.login_name,'new')
        self.assertFalse(s.capability(user,'GLOBAL_EDIT'))
        history=' '.join(str(e.after) for e in m.OperationEvent.objects.filter(target_id=user.pk))
        self.assertNotIn('private',history);self.assertNotIn('pbkdf2',history)
        cmd=uuid.uuid4();data={'login_name':'retry','password':'first'}
        s.execute(self.a,cmd,'create','employee',data=data)
        self.forbidden(lambda:s.execute(self.a,cmd,'create','employee',data={**data,'password':'second'}),'COMMAND_KEY_REUSED')
        self.command(user,is_active=False)
        user.refresh_from_db()
        self.forbidden(lambda:self.create('company',actor=user),'AUTH_REQUIRED')

    def test_AT15_edit_event_authorship_is_preserved(self):
        company=self.create('company')
        self.grant(self.b,'GLOBAL_EDIT')
        self.command(company,actor=self.b,name='B更正')
        events=m.OperationEvent.objects.filter(target_id=company.pk)
        self.assertEqual(set(events.values_list('actor_id',flat=True)),{self.a.pk,self.b.pk})
        self.assertTrue(all(e.happened_at for e in events))

    def test_AT17_AT18_current_counts_and_taipei_boundary(self):
        company=self.create('company');person=self.create('contact')
        opp=self.create('opportunity',company=company,participants=[person])
        self.create('enquiry',opportunity=opp);self.create('enquiry',opportunity=opp)
        self.command(opp,'won',reason='確認成交')
        self.assertEqual(statistics()['opportunity_states'][3][1][0].pk,opp.pk)
        self.command(opp,'reopen');self.command(opp,'lost',reason='確認未成交');self.command(opp,'reopen')
        stat=statistics();self.assertEqual(stat['opportunity_total'],1)
        self.assertEqual(sum(total for _,_,states in stat['stage_matrix'] for _,_,total in states),1)
        self.assertEqual(sum(len(rows) for _,rows,_ in stat['opportunity_states']),1)
        self.assertEqual(len(stat['opportunity_states'][3][1])+len(stat['opportunity_states'][4][1]),0)
        self.create('task',due_on=date(2026,11,22));self.create('task')
        finished=self.create('task',due_on=date(2026,11,22));self.command(finished,'complete')
        before=statistics(datetime(2026,11,22,15,59,59,tzinfo=dt_timezone.utc))
        after=statistics(datetime(2026,11,22,16,0,tzinfo=dt_timezone.utc))
        self.assertEqual((before['due'],before['overdue']),(1,0))
        self.assertEqual((after['due'],after['overdue']),(0,1))

    def test_AT20_preview_new_work_and_duplicate_command(self):
        company=self.create('company')
        token,rows=s.preview(self.a,'company',company.pk,'deactivate')
        self.create('task',subject_company=company)
        self.forbidden(lambda:self.command(company,'deactivate',preview=token,decisions={}), 'PREVIEW_CHANGED')
        company.refresh_from_db();self.assertTrue(company.is_active)
        opp=self.create('opportunity',company=company);cmd=uuid.uuid4()
        args=(self.a,cmd,'won','opportunity',opp.pk,opp.version,{'reason':'原因'})
        first=s.execute(*args);second=s.execute(*args)
        self.assertEqual(first.pk,second.pk)
        self.assertEqual(m.OperationEvent.objects.filter(target_id=opp.pk,operation='won').count(),1)

    def test_AT20_rollback_after_core_and_before_receipt(self):
        company=self.create('company',name='原值')
        self.create('opportunity',company=company)
        version=company.version;count=m.OperationEvent.objects.count()
        with patch('crm.services.event',side_effect=RuntimeError('injected after core write')):
            with self.assertRaises(RuntimeError):self.command(company,name='不應保存')
        company.refresh_from_db();self.assertEqual((company.name,company.version),('原值',version))
        self.assertEqual(m.OperationEvent.objects.count(),count)
        with patch.object(m.CommandReceipt.objects,'create',side_effect=RuntimeError('injected before receipt')):
            with self.assertRaises(RuntimeError):self.preview_command(company,'deactivate')
        company.refresh_from_db();self.assertTrue(company.is_active)
        self.assertEqual(m.ContinuationGrant.objects.count(),0)
        self.assertEqual(m.OperationEvent.objects.count(),count)

    def test_AT20_affiliation_failure_restores_current_period(self):
        first=self.create('company');second=self.create('company')
        contact=self.create('contact',company_affiliation=first)
        version=contact.version;events=m.OperationEvent.objects.count();receipts=m.CommandReceipt.objects.count()
        with patch.object(m.ContactAffiliation.objects,'create',side_effect=RuntimeError('injected after old affiliation ended')):
            with self.assertRaises(RuntimeError):self.command(contact,company_affiliation=second)
        contact.refresh_from_db()
        self.assertEqual(contact.version,version)
        self.assertEqual(contact.affiliations.count(),1)
        self.assertEqual(contact.affiliations.get(ended_at=None).company,first)
        self.assertEqual(m.OperationEvent.objects.count(),events)
        self.assertEqual(m.CommandReceipt.objects.count(),receipts)

    def test_AT20_database_constraints_are_real(self):
        company=self.create('company');contact=self.create('contact',company_affiliation=company)
        evt=m.OperationEvent.objects.first()
        with self.assertRaises(IntegrityError),transaction.atomic():
            m.ContactAffiliation.objects.create(contact=contact,company=company,started_at=evt.happened_at,created_event=evt)
        with self.assertRaises(IntegrityError),transaction.atomic():
            m.Task.objects.create(created_by=self.a,subject_company=company,subject_enquiry=self.create('enquiry'))

class HttpTests(Helpers,TestCase):
    def setUp(self):self.setup_people();self.client.force_login(self.b)
    def test_AT13_AT16_all_core_reading_and_no_delete(self):
        company=self.create('company',name='中文搜尋')
        task=self.create('task',title='內部整理')
        self.assertContains(self.client.get(reverse('record_list',args=['company']),{'q':'中文'}),'中文搜尋')
        self.assertContains(self.client.get(reverse('detail',args=['task',task.pk])),'內部整理')
        self.assertEqual(self.client.delete(reverse('detail',args=['company',company.pk])).status_code,405)
        self.assertEqual(self.client.get(reverse('record_list',args=['employee'])).status_code,403)
        self.assertEqual(self.client.get('/').status_code,200)
    def test_AT14_direct_forged_or_unauthorized_requests(self):
        company=self.create('company')
        url=reverse('edit',args=['company',company.pk])
        self.assertEqual(self.client.post(url,{'name':'未授權'}).status_code,403)
        self.client.force_login(self.a)
        data={'command_id':uuid.uuid4(),'expected_version':company.version,'name':'修改','owner':self.b.pk}
        self.assertEqual(self.client.post(url,data).status_code,422)
        company.refresh_from_db();self.assertEqual(company.name,'')
        data.pop('owner');data['name']=['第二','第三']
        self.assertEqual(self.client.post(url,data).status_code,422)
    def test_AT02_AT19_duplicate_ui_and_nearby_errors(self):
        self.create('company',name='重複')
        self.client.force_login(self.a)
        url=reverse('new',args=['company']);data={'name':'重複','command_id':uuid.uuid4()}
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,422);self.assertContains(response,'疑似重複公司',status_code=422)
        self.assertContains(response,'role="alert"',status_code=422)
        response=self.client.post(url,{**data,'ack_duplicates':'on'})
        self.assertEqual(response.status_code,302);self.assertEqual(m.Company.objects.count(),2)
    def test_forms_valid_save_each_core_and_cancel_control(self):
        self.client.force_login(self.a)
        company=self.create('company');contact=self.create('contact')
        for kind,data in [('company',{'name':'Web公司'}),('contact',{'name':'Web聯絡人','company_affiliation':company.pk}),('enquiry',{'subject':'Web詢問'}),('opportunity',{'company':company.pk,'stage':'PRICE_NEGOTIATION','participants':[contact.pk]}),('task',{'title':'Web待辦'}),('interaction',{'content':'Web通話','fact_confirmed':'on'})]:
            url=reverse('new',args=[kind])
            self.assertContains(self.client.get(url),'data-cancel=')
            response=self.client.post(url,{**data,'command_id':uuid.uuid4()})
            self.assertEqual(response.status_code,302,(kind,response.content[:1500]))
        self.grant(self.a,'ACCOUNT_ADMIN')
        response=self.client.post(reverse('new',args=['employee']),{'login_name':'web','password':'private','is_active':'on','capabilities':['TRANSFER'],'command_id':uuid.uuid4()})
        self.assertEqual(response.status_code,302,response.content[:1500])
    def test_action_forms_confirm_outcome_and_deactivation(self):
        self.client.force_login(self.a)
        company=self.create('company');opp=self.create('opportunity',company=company)
        response=self.client.get(reverse('action',args=['opportunity',opp.pk,'won']))
        form=response.context['form']
        data={'command_id':form['command_id'].value(),'expected_version':opp.version,'reason':'實際原因'}
        response=self.client.post(reverse('action',args=['opportunity',opp.pk,'won']),data)
        self.assertEqual(response.status_code,302,response.content[:1500])
        response=self.client.get(reverse('action',args=['company',company.pk,'deactivate']))
        form=response.context['form'];data={name:field.value() for name,field in [(name,form[name]) for name in form.fields] if form[name].value() is not None}
        response=self.client.post(reverse('action',args=['company',company.pk,'deactivate']),data)
        self.assertEqual(response.status_code,302,response.content[:1500])
    def test_csrf_and_inactive_session(self):
        secure=Client(enforce_csrf_checks=True);secure.force_login(self.a)
        self.assertEqual(secure.post(reverse('new',args=['company']),{'command_id':uuid.uuid4()}).status_code,403)
        self.b.is_active=False;self.b.save()
        self.assertEqual(self.client.get('/').status_code,302)
    def test_primary_subject_feedback_is_plain_language(self):
        self.client.force_login(self.a)
        company=self.create('company');opp=self.create('opportunity',company=company)
        response=self.client.post(reverse('new',args=['task']),{'command_id':uuid.uuid4(),'subject_company':company.pk,'subject_opportunity':opp.pk})
        self.assertContains(response,'主要業務主題最多選擇一個',status_code=422)
        self.assertNotContains(response,'task_one_subject',status_code=422)

class ConcurrencyTests(Helpers,TransactionTestCase):
    def setUp(self):self.setup_people()
    def race(self,*jobs):
        barrier=Barrier(len(jobs))
        def run(fn):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                fn()
                return 'OK'
            except s.BusinessError as exc:return exc.code
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:return list(pool.map(run,jobs))
    def test_AT20_simultaneous_affiliation_changes(self):
        company=self.create('company');other=self.create('company');contact=self.create('contact')
        version=contact.version
        def job(c):return lambda:s.execute(self.a,uuid.uuid4(),'edit','contact',contact.pk,version,{'company_affiliation':c})
        outcomes=self.race(job(company),job(other))
        self.assertCountEqual(outcomes,['OK','VERSION_CONFLICT'])
        self.assertEqual(contact.affiliations.filter(ended_at=None).count(),1)
    def test_AT20_deactivation_racing_opportunity_creation(self):
        company=self.create('company');token,_=s.preview(self.a,'company',company.pk,'deactivate')
        outcomes=self.race(lambda:s.execute(self.a,uuid.uuid4(),'deactivate','company',company.pk,company.version,{'preview':token,'decisions':{}}),lambda:self.create('opportunity',company=company))
        company.refresh_from_db()
        if company.is_active:self.assertCountEqual(outcomes,['PREVIEW_CHANGED','OK']);self.assertEqual(m.Opportunity.objects.count(),1)
        else:self.assertCountEqual(outcomes,['OK','COMPANY_INACTIVE']);self.assertEqual(m.Opportunity.objects.count(),0)
    def test_AT20_transfer_racing_former_owner_edit(self):
        company=self.create('company');self.grant(self.a,'TRANSFER')
        self.preview_command(company,'transfer',new_owner=str(self.b.pk))
        company.refresh_from_db();version=company.version
        token,_=s.preview(self.a,'company',company.pk,'transfer')
        outcomes=self.race(lambda:s.execute(self.a,uuid.uuid4(),'transfer','company',company.pk,version,{'preview':token,'decisions':{f'company:{company.pk}':'transfer'},'new_owner':str(self.c.pk)}),lambda:s.execute(self.b,uuid.uuid4(),'edit','company',company.pk,version,{'name':'舊頁面'}))
        self.assertEqual(outcomes.count('OK'),1)
        self.assertIn('VERSION_CONFLICT',outcomes)
    def test_AT20_same_confirmation_retry_different_connections(self):
        company=self.create('company');opp=self.create('opportunity',company=company);cmd=uuid.uuid4()
        job=lambda:s.execute(self.a,cmd,'won','opportunity',opp.pk,opp.version,{'reason':'人員確認'})
        self.assertEqual(self.race(job,job),['OK','OK'])
        self.assertEqual(m.OperationEvent.objects.filter(target_id=opp.pk,operation='won').count(),1)

class BootstrapTests(TestCase):
    def test_first_account_is_interactive_attributable_and_not_repeatable(self):
        with self.assertRaises(SystemExit) as exc:call_command('needs_bootstrap')
        self.assertEqual(exc.exception.code,10)
        output=StringIO()
        with patch('builtins.input',return_value=' First '),patch('crm.management.commands.bootstrap.getpass.getpass',side_effect=['setup-private','setup-private']):
            call_command('bootstrap',stdout=output)
        employee=m.Employee.objects.get()
        self.assertEqual(employee.login_name,'first')
        self.assertTrue(employee.check_password('setup-private'))
        self.assertEqual(employee.capabilities.count(),3)
        self.assertEqual(m.OperationEvent.objects.get().actor,employee)
        self.assertNotIn('setup-private',str(m.OperationEvent.objects.get().after))
        call_command('needs_bootstrap',stdout=StringIO())
        with self.assertRaises(CommandError):call_command('bootstrap',stdout=StringIO())
        self.assertEqual(m.Employee.objects.count(),1)
