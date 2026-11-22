"""All business writes, authorization, evidence and retry boundaries live here."""
import hashlib
import hmac
import json
import unicodedata
import uuid
from datetime import date, datetime
from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from .models import (Company, Contact, Enquiry, Opportunity, Interaction, Task, Employee,
    EmployeeGrant, ContactAffiliation, OpportunityContact, OperationEvent, ContinuationGrant, CommandReceipt)

MODELS = {'company': Company, 'contact': Contact, 'enquiry': Enquiry, 'opportunity': Opportunity,
          'interaction': Interaction, 'task': Task, 'employee': Employee}
LABELS = {'company':'公司','contact':'聯絡人','enquiry':'詢問','opportunity':'商機','interaction':'實際互動','task':'追蹤待辦','employee':'員工帳號'}
FIELDS = {
 'company':['name','phone','email','address','tax_identifier','notes'],
 'contact':['name','phone','email','notes'],
 'enquiry':['subject','content','source_note','known_contact_name','known_phone','known_email','company','opportunity','qualification_note'],
 'opportunity':['company','title','need_description','stage'],
 'interaction':['content','participant_note','occurred_at','fact_confirmed','subject_company','subject_enquiry','subject_opportunity','continuation_grant'],
 'task':['title','description','scheduled_at','due_on','subject_company','subject_enquiry','subject_opportunity','continuation_grant'],
 'employee':['login_name','display_name','is_active'],
}

class BusinessError(Exception):
    def __init__(self, message, code='VALIDATION_ERROR', status=422):
        self.message, self.code, self.status = message, code, status
        super().__init__(message)

def fail(message, code='VALIDATION_ERROR', status=422):
    raise BusinessError(message, code, status)

def capability(actor, name):
    return bool(actor and actor.is_active and EmployeeGrant.objects.filter(employee_id=actor.pk, capability=name).exists())

def can_edit(actor, obj):
    return actor.is_active and (obj.created_by_id == actor.pk or obj.owner_id == actor.pk or capability(actor,'GLOBAL_EDIT'))

def authorize(actor, obj=None, special=None):
    if not actor.is_active:
        fail('帳號已停用，請重新登入。','AUTH_REQUIRED',401)
    if special and not capability(actor,special):
        fail('此作業需要獨立的專項授權。','PERMISSION_DENIED',403)
    if obj is not None and special is None and not can_edit(actor,obj):
        fail('僅建立者、目前負責人或具全域編輯授權者可編輯。','PERMISSION_DENIED',403)

def snapshot(obj):
    safe = {}
    for field in obj._meta.concrete_fields:
        if field.name in ['password','last_login']:
            continue
        safe[field.attname] = getattr(obj,field.attname)
    return json.loads(json.dumps(safe, cls=DjangoJSONEncoder))

def event(actor, cmd, op, kind, obj, before=None, reason='', after=None):
    return OperationEvent.objects.create(actor=actor,command_id=cmd,operation=op,target_kind=kind,target_id=obj.pk,
        before=before or {},after=after if after is not None else snapshot(obj),reason=reason)

def kind_of(obj):
    return next(k for k,v in MODELS.items() if isinstance(obj,v))

def company_for(obj):
    if isinstance(obj,Company): return obj
    if isinstance(obj,Opportunity): return obj.company
    if isinstance(obj,Enquiry): return obj.company or (obj.opportunity.company if obj.opportunity_id else None)
    if isinstance(obj,(Task,Interaction)):
        subject = obj.subject_company or obj.subject_enquiry or obj.subject_opportunity
        return company_for(subject) if subject else None
    return None

def subject_key(obj):
    for kind in ['company','enquiry','opportunity']:
        value=getattr(obj,'subject_'+kind+'_id',None)
        if value: return kind,str(value)
    return None

def context_chain(obj):
    chain={(kind_of(obj),str(obj.pk))}
    if isinstance(obj,Enquiry) and obj.opportunity_id:
        chain |= context_chain(obj.opportunity)
    if isinstance(obj,(Task,Interaction)):
        key=subject_key(obj)
        if key: chain |= context_chain(MODELS[key[0]].objects.get(pk=key[1]))
    company=company_for(obj)
    if company: chain.add(('company',str(company.pk)))
    return chain

def valid_grant(grant, company, obj):
    if not grant or grant.company_id != company.pk or grant.deactivation_event_id != company.deactivation_event_id:
        return False
    work = grant.enquiry or grant.opportunity or grant.task
    actual=company_for(work)
    if not actual or actual.pk != company.pk: return False
    if isinstance(obj,(Enquiry,Opportunity)):
        return type(obj) is type(work) and obj.pk==work.pk
    if isinstance(obj,Task) and grant.task_id==obj.pk:
        # At edit time the grant's source is freshly read from the database.
        return subject_key(obj)==subject_key(work)
    return subject_key(obj) in context_chain(work)

def continuation_guard(obj, creating=False):
    company=company_for(obj)
    if not company or company.is_active or isinstance(obj,(Company,Contact)):
        return
    if creating and isinstance(obj,Enquiry): return  # intake is still permitted
    grants=[]
    if not creating and isinstance(obj,(Enquiry,Opportunity,Task)):
        grants=list(ContinuationGrant.objects.filter(company=company,**{kind_of(obj):obj}))
    if isinstance(obj,(Task,Interaction)) and obj.continuation_grant_id:
        grants.append(ContinuationGrant.objects.get(pk=obj.continuation_grant_id))
    # Historical interaction corrections are not unfinished-work progression.
    if not creating and isinstance(obj,Interaction): return
    if not any(valid_grant(g,company,obj) for g in grants):
        fail('此公司已停用，須先明確確認相關工作續辦；新待辦或互動請選擇相符續辦依據。','CONTINUATION_REQUIRED',409)

def check_version(obj, expected):
    try: matched=obj.version==int(expected)
    except (ValueError,TypeError): matched=False
    if not matched: fail('資料已更新，請查看最新版本並重新確認。','VERSION_CONFLICT',409)

def validate(obj):
    if isinstance(obj,(Task,Interaction)) and sum(bool(getattr(obj,'subject_'+k+'_id')) for k in ['company','enquiry','opportunity'])>1:
        fail('主要業務主題最多選擇一個，請只保留公司、詢問或商機其中一項。')
    if isinstance(obj,Interaction) and not obj.fact_confirmed:
        fail('請明確勾選確認此互動已實際發生。')
    if isinstance(obj,Enquiry) and obj.company_id and obj.opportunity_id and obj.company_id!=obj.opportunity.company_id:
        fail('直接公司必須與商機的主要公司相同；也可以只連結商機。')
    if isinstance(obj,Opportunity):
        conflicts=obj.enquiries.exclude(company_id=None).exclude(company_id=obj.company_id) if not obj._state.adding else []
        if conflicts: fail('已有詢問的直接公司與此主要公司不同，請先經授權更正詢問連結。')
    try: obj.full_clean(exclude=['created_at','updated_at','last_login'])
    except ValidationError as exc: fail('；'.join(exc.messages))

def normalized(value):
    return unicodedata.normalize('NFKC',value).strip().casefold()

def duplicates(data, exclude=None):
    matches=[]
    for c in Company.objects.exclude(pk=exclude):
        reasons=[label for key,label in [('name','名稱'),('phone','電話'),('email','電郵')]
                 if data.get(key) and normalized(str(data[key])) and normalized(str(data[key]))==normalized(getattr(c,key))]
        if reasons: matches.append((c,'、'.join(reasons)))
    return matches

def affected(company):
    rows=[]
    for kind,qs in [('enquiry',Enquiry.objects.filter(status='OPEN')),('opportunity',Opportunity.objects.filter(status__in=['ACTIVE','HELD'])),('task',Task.objects.filter(status='OPEN'))]:
        for obj in qs:
            c=company_for(obj)
            if c and c.pk==company.pk: rows.append((kind,obj))
    return sorted(rows,key=lambda x:(x[0],str(x[1].pk)))

def preview_state(kind,obj):
    company=company_for(obj)
    rows=affected(company) if company else [(kind,obj)]
    if (kind,str(obj.pk)) not in [(k,str(v.pk)) for k,v in rows]: rows.insert(0,(kind,obj))
    state={'kind':kind,'id':str(obj.pk),'version':obj.version,
           'company':str(company.pk) if company else None,
           'company_version':company.version if company else None,
           'items':[(k,str(v.pk),v.version,str(v.owner_id),getattr(v,'status','')) for k,v in rows]}
    return state,rows

def preview(actor,kind,pk,op):
    with transaction.atomic():
        actor=Employee.objects.get(pk=actor.pk)
        obj=MODELS[kind].objects.get(pk=pk)
        authorize(actor,obj if op!='transfer' else None,'TRANSFER' if op=='transfer' else None)
        state,rows=preview_state(kind,obj)
        return signing.dumps(state,salt='crm-preview'),rows

def check_preview(kind,obj,token):
    try: previous=signing.loads(token,salt='crm-preview')
    except signing.BadSignature: fail('確認資料無效，請重新預覽。','PREVIEW_CHANGED',409)
    state,rows=preview_state(kind,obj)
    # Signing serializes tuples as lists.
    state=json.loads(json.dumps(state))
    if previous!=state: fail('受影響工作已變動，請重新預覽並確認全部項目。','PREVIEW_CHANGED',409)
    return rows

def _apply_fields(obj,kind,data):
    for key in FIELDS[kind]:
        if key not in data: continue
        field=obj._meta.get_field(key)
        value=data[key]
        if field.is_relation:
            setattr(obj,field.attname, value.pk if isinstance(value,field.related_model) else (value or None))
        else: setattr(obj,key,value)

def _serialize(value):
    if hasattr(value,'pk'): return str(value.pk)
    if isinstance(value,(uuid.UUID,date,datetime)): return str(value)
    if isinstance(value,dict): return {k:_serialize(v) for k,v in value.items()}
    if isinstance(value,(QuerySet,set)): return sorted([_serialize(v) for v in value],key=str)
    if isinstance(value,(list,tuple)): return [_serialize(v) for v in value]
    return value

def execute(actor, command_id, operation, kind, pk=None, expected=None, data=None):
    """One serialized write transaction. Never catch DB failures inside this boundary."""
    data=data or {}
    if kind not in MODELS: fail('未知紀錄類別。')
    try: command_id=uuid.UUID(str(command_id))
    except (ValueError,TypeError): fail('送出識別無效，請重新開啟表單。')
    fingerprint=hmac.new(settings.SECRET_KEY.encode(),json.dumps(_serialize([operation,kind,pk,expected,data]),sort_keys=True,ensure_ascii=False).encode(),hashlib.sha256).hexdigest()
    with transaction.atomic():
        actor=Employee.objects.get(pk=actor.pk)
        authorize(actor,special='ACCOUNT_ADMIN' if kind=='employee' else None)
        old=CommandReceipt.objects.filter(actor=actor,command_id=command_id).first()
        if old:
            if not hmac.compare_digest(old.request_fingerprint,fingerprint): fail('相同送出識別不能用於不同內容。','COMMAND_KEY_REUSED',409)
            return MODELS[old.result_kind].objects.get(pk=old.result_id)
        allowed=set(FIELDS[kind])|{'participants','company_affiliation','password','capabilities','preview','decisions','new_owner','reason','stage','grant','ack_duplicates'}
        if set(data)-allowed: fail('包含不可直接變更的欄位。')
        creating=operation=='create'
        obj=MODELS[kind]() if creating else MODELS[kind].objects.get(pk=pk)
        before={} if creating else snapshot(obj)
        if not creating:
            check_version(obj,expected)
            if operation=='transfer': authorize(actor,special='TRANSFER')
            elif kind!='employee': authorize(actor,obj)
            if operation not in ['transfer','deactivate','reactivate','continue'] and kind!='employee': continuation_guard(obj)
        if creating or operation=='edit':
            if kind=='opportunity' and not creating and data.get('stage',obj.stage)!=obj.stage and obj.status!='ACTIVE':
                fail('請先恢復進行或重開商機，再調整階段。','VERSION_CONFLICT',409)
            affiliation_before=None
            participants_before=[]
            if kind=='contact' and not creating:
                affiliation_before=obj.affiliations.filter(ended_at=None).values_list('company_id',flat=True).first()
            if kind=='opportunity' and not creating:
                participants_before=list(obj.participations.filter(removed_event=None).values_list('contact_id',flat=True))
            _apply_fields(obj,kind,data)
            if kind=='employee':
                from .auth import normalize_login
                obj.login_name=normalize_login(obj.login_name)
                if not obj.login_name: fail('登入帳號不可空白。')
                if creating and not data.get('password'): fail('建立員工帳號時請設定密碼。')
                if data.get('password'): obj.set_password(data['password'])
            else:
                if creating: obj.created_by=actor; obj.owner=actor
                if kind=='opportunity' and (creating or str(obj.company_id)!=str(before.get('company_id'))):
                    if not obj.company_id:fail('每個商機必須指定一個主要公司。')
                    if not obj.company.is_active: fail('停用公司不能新增商機，請先啟用公司。','COMPANY_INACTIVE',409)
                continuation_guard(obj,creating)
            validate(obj)
            if not creating: obj.version+=1
            obj.save()
            audit=event(actor,command_id,operation,kind,obj,before)
            if kind=='employee' and 'capabilities' in data:
                caps=set(data['capabilities'])
                if not caps <= {v[0] for v in EmployeeGrant.CAPABILITIES}: fail('未知授權。')
                prior=set(EmployeeGrant.objects.filter(employee=obj).values_list('capability',flat=True))
                EmployeeGrant.objects.filter(employee=obj).exclude(capability__in=caps).delete()
                for cap in caps-prior: EmployeeGrant.objects.create(employee=obj,capability=cap,granted_by=actor)
                audit.before['capabilities']=sorted(prior);audit.after['capabilities']=sorted(caps);audit.save()
            if kind=='contact' and 'company_affiliation' in data:
                company=data['company_affiliation']
                company_id=company.pk if isinstance(company,Company) else company or None
                current=ContactAffiliation.objects.filter(contact=obj,ended_at=None).first()
                if str(current.company_id if current else None)!=str(company_id):
                    now=timezone.now()
                    if current: current.ended_at=now;current.ended_event=audit;current.save()
                    if company_id: ContactAffiliation.objects.create(contact=obj,company_id=company_id,started_at=now,created_event=audit)
                audit.before['affiliation']=str(affiliation_before) if affiliation_before else None
                audit.after['affiliation']=str(company_id) if company_id else None
                audit.save()
            if kind=='opportunity' and 'participants' in data:
                ids={str(p.pk if isinstance(p,Contact) else p) for p in data['participants']}
                if Contact.objects.filter(pk__in=ids).count()!=len(ids): fail('參與聯絡人不存在。')
                existing=OpportunityContact.objects.filter(opportunity=obj,removed_event=None)
                prior={str(p.contact_id) for p in existing}
                existing.exclude(contact_id__in=ids).update(removed_event=audit)
                for contact_id in ids-prior: OpportunityContact.objects.create(opportunity=obj,contact_id=contact_id,added_event=audit)
                audit.before['participants']=sorted(str(p) for p in participants_before)
                audit.after['participants']=sorted(ids);audit.save()
            if kind=='interaction':
                prior_subject=next(((k,before.get('subject_'+k+'_id')) for k in ['company','enquiry','opportunity'] if before.get('subject_'+k+'_id')),None)
                if creating or prior_subject!=subject_key(obj):
                    c=company_for(obj)
                    obj.context_snapshot={'subject':list(subject_key(obj)) if subject_key(obj) else None,'company_id':str(c.pk) if c else None,'company_name':str(c) if c else None}
                else:
                    obj.context_snapshot=before.get('context_snapshot',{})
                obj.save(update_fields=['context_snapshot'])
                audit.after=snapshot(obj);audit.save()
                # Prior snapshot remains in audit.before on every correction.
        elif operation in ['deactivate','reactivate','continue','transfer']:
            if operation!='transfer' and kind!='company': fail('此動作只適用公司。')
            rows=check_preview(kind,obj,data.get('preview','')) if operation!='reactivate' else []
            decisions=data.get('decisions',{})
            keys={f'{k}:{v.pk}' for k,v in rows}
            if operation in ['deactivate','continue']:
                if (operation=='deactivate') != obj.is_active: fail('公司狀態已變動，請使用啟用或續辦作業。','VERSION_CONFLICT',409)
                workrows=[(k,v) for k,v in rows if k in ['enquiry','opportunity','task']]
                if set(decisions)!= {f'{k}:{v.pk}' for k,v in workrows} or not set(decisions.values()) <= {'continue','freeze'}:
                    fail('請逐項明確選擇續辦或保留尚未確認，不得漏項。')
                if operation=='continue':
                    for k,v in workrows:
                        if decisions[f'{k}:{v.pk}']=='freeze' and ContinuationGrant.objects.filter(company=obj,deactivation_event=obj.deactivation_event,**{k:v}).exists():
                            fail('此工作先前已確認續辦；本作業不會撤回既有確認，請保留續辦選項。')
                if operation=='deactivate':
                    obj.is_active=False
                    audit=event(actor,command_id,operation,kind,obj,before,after={**snapshot(obj),'decisions':decisions})
                    obj.deactivation_event=audit
                for k,v in workrows:
                    if decisions[f'{k}:{v.pk}']=='continue':
                        ContinuationGrant.objects.get_or_create(company=obj,deactivation_event=obj.deactivation_event,**{k:v},defaults={'confirmed_by':actor,'source_version':v.version})
                obj.version+=1;obj.save()
                if operation=='continue': audit=event(actor,command_id,operation,kind,obj,before,after={**snapshot(obj),'decisions':decisions})
                else: audit.after={**snapshot(obj),'decisions':decisions};audit.save()
            elif operation=='reactivate':
                if obj.is_active: fail('公司目前已啟用。','VERSION_CONFLICT',409)
                obj.is_active=True;obj.deactivation_event=None;obj.version+=1;obj.save();event(actor,command_id,operation,kind,obj,before)
            else:
                if set(decisions)-keys: fail('選取的轉交項目不在本次預覽中。')
                if decisions.get(f'{kind}:{obj.pk}')!='transfer': fail('請明確選取本筆責任轉交。')
                owner=data.get('new_owner') or None
                if owner and not Employee.objects.filter(pk=owner,is_active=True).exists(): fail('新負責人帳號無效。')
                for k,v in rows:
                    if decisions.get(f'{k}:{v.pk}')=='transfer':
                        prior=snapshot(v);v.owner_id=owner;v.version+=1;v.save();event(actor,command_id,operation,k,v,prior)
                obj.refresh_from_db()
        else:
            reason=str(data.get('reason','')).strip()
            if kind=='enquiry' and operation in ['qualify','close']:
                if obj.status!='OPEN': fail('詢問已結束，不能重複處理。','VERSION_CONFLICT',409)
                if operation=='qualify': obj.qualification_note=reason;obj.qualified_at=timezone.now();obj.qualified_by=actor
                else: obj.status='CLOSED';obj.closed_at=timezone.now();obj.closed_by=actor;obj.closure_note=reason
            elif kind=='opportunity' and operation in ['hold','resume','cancel','won','lost','reopen']:
                valid={'hold':['ACTIVE'],'resume':['HELD'],'cancel':['ACTIVE','HELD'],'won':['ACTIVE','HELD'],'lost':['ACTIVE','HELD'],'reopen':['WON','LOST','CANCELLED']}
                if obj.status not in valid[operation]: fail('目前商機狀態不允許此動作，請重新讀取。','VERSION_CONFLICT',409)
                obj.status={'hold':'HELD','resume':'ACTIVE','cancel':'CANCELLED','won':'WON','lost':'LOST','reopen':'ACTIVE'}[operation]
                if operation in ['won','lost']:
                    if not reason: fail('確認成交或未成交時必須輸入原因。')
                    obj.current_outcome_reason=reason;obj.current_outcome_at=timezone.now();obj.current_outcome_by=actor
                else:
                    obj.current_outcome_reason='';obj.current_outcome_at=None;obj.current_outcome_by=None
                if operation in ['resume','reopen']: obj.stage=data.get('stage',obj.stage)
            elif kind=='task' and operation in ['complete','cancel']:
                if obj.status!='OPEN': fail('待辦已結束，不能重複完成或取消。','VERSION_CONFLICT',409)
                obj.status='COMPLETED' if operation=='complete' else 'CANCELLED';obj.finished_at=timezone.now();obj.finished_by=actor
            else: fail('未知或不適用的作業。')
            validate(obj);obj.version+=1;obj.save();event(actor,command_id,operation,kind,obj,before,reason)
        CommandReceipt.objects.create(actor=actor,command_id=command_id,operation=operation,request_fingerprint=fingerprint,result_kind=kind,result_id=obj.pk)
        return obj
