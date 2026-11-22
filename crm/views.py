import logging
import uuid
from django import forms
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.http import Http404, HttpResponseNotAllowed
from django.http import FileResponse
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from . import services as s, models as m
from .forms import record_form,action_form,ACTION_LABELS,ZH

log=logging.getLogger(__name__)

def context(request,**extra):
    return {'nav':[(k,v) for k,v in s.LABELS.items() if k!='employee' or s.capability(request.user,'ACCOUNT_ADMIN')],
            'can_export_system':s.capability(request.user,'ACCOUNT_ADMIN'),**extra}

@login_required
def export_system(request):
    from .packaging import PackageError,inspect_release,create_archive
    from .release import APP_VERSION
    if request.method not in ['GET','POST']:return HttpResponseNotAllowed(['GET','POST'])
    try:s.authorize(request.user,special='ACCOUNT_ADMIN')
    except s.BusinessError as exc:return error_page(request,exc)
    form=forms.Form(request.POST if request.method=='POST' else None)
    form.fields['command_id']=forms.UUIDField(initial=uuid.uuid4,widget=forms.HiddenInput())
    status=200
    if request.method=='POST':
        archive=None
        try:
            validate_post(request,form)
            if form.is_valid():
                release=inspect_release(settings.CRM_EXPORT_EXECUTABLE,settings.CRM_EXPORT_METADATA)
                archive=create_archive(release,settings.CRM_PORTABLE_ASSETS,settings.RUNTIME/'exports')
                with transaction.atomic():
                    actor=m.Employee.objects.get(pk=request.user.pk)
                    s.authorize(actor,special='ACCOUNT_ADMIN')
                    audit,created=m.OperationEvent.objects.get_or_create(actor=actor,command_id=form.cleaned_data['command_id'],operation='export_system',
                        defaults={'target_kind':'system','target_id':uuid.uuid5(uuid.NAMESPACE_URL,'04-CRM/system-export'),
                                  'after':{'version':APP_VERSION,'sha256':release.sha256,'contains_business_data':False},
                                  'reason':'匯出獨立可執行系統（全新系統，不含目前帳號與業務資料）'})
                    if not created and audit.after.get('sha256')!=release.sha256:
                        s.fail('系統包已更新，請重新開啟匯出頁。','COMMAND_KEY_REUSED',409)
                response=FileResponse(archive,as_attachment=True,filename=f'CRM-Windows-x64-{APP_VERSION}.zip',content_type='application/zip')
                response['Cache-Control']='no-store'
                return response
            status=422
        except (PackageError,OSError) as exc:
            if archive:archive.close()
            form.add_error(None,str(exc) if isinstance(exc,PackageError) else '匯出未完成，請稍後再試。')
            status=503
        except (s.BusinessError,IntegrityError,OperationalError,ValidationError) as exc:
            if archive:archive.close()
            status=add_error(form,exc)
    return render(request,'export.html',context(request,form=form,version=APP_VERSION),status=status)

def model_for(request,kind):
    if kind not in s.MODELS: raise Http404()
    if kind=='employee' and not s.capability(request.user,'ACCOUNT_ADMIN'):
        raise s.BusinessError('此頁需要帳號管理授權。','PERMISSION_DENIED',403)
    return s.MODELS[kind]

def error_page(request,exc):
    return render(request,'error.html',context(request,error=exc.message,code=exc.code),status=exc.status)

def validate_post(request,form):
    unexpected=set(request.POST)-set(form.fields)-{'csrfmiddlewaretoken'}
    many={name for name in request.POST if len(request.POST.getlist(name))>1 and not isinstance(form.fields.get(name),(forms.MultipleChoiceField,forms.ModelMultipleChoiceField))}
    if unexpected or many: raise s.BusinessError('包含未允許欄位或單一欄位送入多個值。')

def add_error(form,exc):
    if isinstance(exc,s.BusinessError):
        form.add_error(None,exc.message)
        return exc.status
    if isinstance(exc,OperationalError):
        form.add_error(None,'資料正在寫入，請稍後使用同一表單重試。')
        return 503
    if isinstance(exc,(IntegrityError,ValidationError)):
        form.add_error(None,'資料關聯或狀態不符合要求，請重新檢查。')
        return 422
    raise exc

def sign_in(request):
    if request.user.is_authenticated: return redirect('dashboard')
    error=''
    if request.method=='POST':
        user=authenticate(request,username=request.POST.get('username',''),password=request.POST.get('password',''))
        if user: login(request,user);return redirect('dashboard')
        error='帳號或密碼不正確，或帳號已停用。'
    return render(request,'login.html',{'error':error,'needs_setup':not m.Employee.objects.exists()})

def sign_out(request):
    if request.method!='POST':return HttpResponseNotAllowed(['POST'])
    logout(request)
    return redirect('login')

def statistics(now=None):
    today=timezone.localdate(now)
    with transaction.atomic():
        opportunities=list(m.Opportunity.objects.all())
        tasks=list(m.Task.objects.all())
        return {'opportunity_total':len(opportunities), 'opportunity_states':[(label,[o for o in opportunities if o.status==code],code) for code,label in m.OPPORTUNITY_STATES],
                'stage_counts':[(label,len([o for o in opportunities if o.stage==code]),code) for code,label in m.STAGES],
                'stage_matrix':[(label,code,[(state_label,state,sum(o.stage==code and o.status==state for o in opportunities)) for state,state_label in m.OPPORTUNITY_STATES]) for code,label in m.STAGES],
                'task_total':len(tasks),'task_states':[(label,[t for t in tasks if t.status==code],code) for code,label in m.Task._meta.get_field('status').choices],
                'due':sum(t.status=='OPEN' and t.due_on==today for t in tasks),
                'overdue':sum(t.status=='OPEN' and t.due_on is not None and t.due_on<today for t in tasks),'today':today}

@login_required
def dashboard(request):
    if request.method not in ['GET','HEAD']:return HttpResponseNotAllowed(['GET','HEAD'])
    stats=statistics()
    return render(request,'dashboard.html',context(request,stats=stats,companies=m.Company.objects.count(),contacts=m.Contact.objects.count(),enquiries=m.Enquiry.objects.filter(status='OPEN').count()))

@login_required
def record_list(request,kind):
    if request.method not in ['GET','HEAD']:return HttpResponseNotAllowed(['GET','HEAD'])
    try:model=model_for(request,kind)
    except s.BusinessError as exc:return error_page(request,exc)
    objects=model.objects.all().order_by('-created_at','pk')
    query=request.GET.get('q','').strip()
    if query:
        condition=Q()
        for field in model._meta.concrete_fields:
            if field.get_internal_type()=='TextField' and field.name not in ['password','login_name']:
                condition |= Q(**{field.name+'__icontains':query})
        try:condition |= Q(pk=uuid.UUID(query))
        except ValueError:pass
        objects=objects.filter(condition)
    status=request.GET.get('status','');stage=request.GET.get('stage','');owner=request.GET.get('owner','');company=request.GET.get('company','')
    if status and hasattr(model,'status'):objects=objects.filter(status=status)
    if stage and kind=='opportunity':objects=objects.filter(stage=stage)
    if owner and kind!='employee':
        try:objects=objects.filter(owner_id=uuid.UUID(owner))
        except ValueError:objects=objects.none()
    if company:
        objects=[obj for obj in objects if (c:=s.company_for(obj)) and str(c.pk)==company]
    due=request.GET.get('due','')
    if due and kind=='task':
        today=timezone.localdate()
        objects=[t for t in objects if t.status=='OPEN' and t.due_on and (t.due_on==today if due=='today' else t.due_on<today)]
    page=Paginator(objects,25).get_page(request.GET.get('page'))
    rows=[{'obj':obj,'url':reverse('detail',args=[kind,obj.pk]),'status':obj.get_status_display() if hasattr(obj,'get_status_display') else ('啟用' if getattr(obj,'is_active',True) else '停用'),
           'stage':obj.get_stage_display() if hasattr(obj,'get_stage_display') else '', 'company':s.company_for(obj),'owner':getattr(obj,'owner',None)} for obj in page]
    params=request.GET.copy();params.pop('page',None)
    choices=model._meta.get_field('status').choices if hasattr(model,'status') else []
    return render(request,'list.html',context(request,kind=kind,label=s.LABELS[kind],rows=rows,page=page,q=query,params=params.urlencode(),statuses=choices,stages=m.STAGES if kind=='opportunity' else [],employees=m.Employee.objects.all(),selected_status=status,selected_stage=stage,selected_owner=owner))

@login_required
def detail(request,kind,pk):
    if request.method not in ['GET','HEAD']:return HttpResponseNotAllowed(['GET','HEAD'])
    try:model=model_for(request,kind)
    except s.BusinessError as exc:return error_page(request,exc)
    obj=get_object_or_404(model,pk=pk)
    editable=kind=='employee' or s.can_edit(request.user,obj)
    actions=[]
    if editable:
        if kind=='company':actions=['deactivate'] if obj.is_active else ['continue','reactivate']
        if kind=='enquiry' and obj.status=='OPEN':actions=['qualify','close']
        if kind=='opportunity':actions={'ACTIVE':['hold','cancel','won','lost'],'HELD':['resume','cancel','won','lost'],'CANCELLED':['reopen'],'WON':['reopen'],'LOST':['reopen']}[obj.status]
        if kind=='task' and obj.status=='OPEN':actions=['complete','cancel']
    if kind!='employee' and s.capability(request.user,'TRANSFER'):actions.append('transfer')
    fields=[]
    for name in list(s.FIELDS[kind])+(['owner','created_by'] if kind!='employee' else [])+['created_at','updated_at','version']:
        value=getattr(obj,name)
        display=getattr(obj,f'get_{name}_display',None)
        fields.append((ZH.get(name,name),display() if display else value))
    for name in ['status','current_outcome_reason','current_outcome_at','current_outcome_by','qualified_at','qualified_by','closed_at','closed_by','closure_note','finished_at','finished_by']:
        if hasattr(obj,name):fields.append((ZH.get(name,name),getattr(obj,'get_status_display')() if name=='status' else getattr(obj,name)))
    if kind=='company':fields.insert(0,('公司狀態','啟用' if obj.is_active else '停用'))
    events=m.OperationEvent.objects.filter(target_kind=kind,target_id=pk).select_related('actor').order_by('-happened_at','-id')
    changes=[]
    code_labels=dict(m.STAGES+m.OPPORTUNITY_STATES+[('OPEN','未完成／未結束'),('CLOSED','已結束'),('COMPLETED','已完成')])
    for e in events:
        diffs=[]
        for key,new in e.after.items():
            old=e.before.get(key)
            if old!=new and key not in ['version','updated_at','created_at','id']:
                def human(value):return code_labels.get(str(value),str(value) if value is not None else '—')
                diffs.append((ZH.get(key.removesuffix('_id'),{'participants':'參與聯絡人','affiliation':'隸屬公司','capabilities':'特殊授權','decisions':'確認處理'}.get(key,key)),human(old),human(new)))
        changes.append((e,diffs))
    related=[]
    if kind=='company':
        for affiliation in m.ContactAffiliation.objects.filter(company=obj).select_related('contact').order_by('-started_at'):
            related.append(('聯絡人',affiliation.contact,reverse('detail',args=['contact',affiliation.contact_id]),'歷史隸屬' if affiliation.ended_at else '目前隸屬'))
        for child in ['enquiry','opportunity','task','interaction']:
            for item in s.MODELS[child].objects.all():
                c=s.company_for(item)
                historical=isinstance(item,m.Interaction) and item.context_snapshot.get('company_id')==str(pk)
                if (c and c.pk==pk) or historical:
                    related.append((s.LABELS[child],item,reverse('detail',args=[child,item.pk]),'當時公司' if historical else '目前關聯（直接或衍生）'))
    return render(request,'detail.html',context(request,kind=kind,label=s.LABELS[kind],obj=obj,fields=fields,editable=editable,actions=[(op,ACTION_LABELS[op]) for op in actions],changes=changes,
        affiliations=obj.affiliations.select_related('company').order_by('-started_at') if kind=='contact' else [],
        participations=obj.participations.select_related('contact').all() if kind=='opportunity' else [],
        current_context=obj.context_snapshot if kind=='interaction' else None,related=related))

@login_required
def edit(request,kind,pk=None):
    if request.method not in ['GET','POST']:return HttpResponseNotAllowed(['GET','POST'])
    try:model=model_for(request,kind)
    except s.BusinessError as exc:return error_page(request,exc)
    obj=get_object_or_404(model,pk=pk) if pk else None
    if obj and kind!='employee' and not s.can_edit(request.user,obj):return error_page(request,s.BusinessError('沒有編輯此紀錄的資格。','PERMISSION_DENIED',403))
    form=record_form(kind,request.POST if request.method=='POST' else None,obj)
    warnings=[];status=200
    if request.method=='POST':
        try:
            validate_post(request,form)
            if form.is_valid():
                data=dict(form.cleaned_data)
                command=data.pop('command_id');expected=data.pop('expected_version')
                if kind=='company':
                    warnings=s.duplicates(data,pk)
                    if warnings and not data.get('ack_duplicates'):raise s.BusinessError('發現疑似重複公司。可取消改選既有紀錄，或勾選確認後繼續保留獨立公司。')
                result=s.execute(request.user,command,'edit' if pk else 'create',kind,pk,expected,data)
                return redirect('detail',kind=kind,pk=result.pk)
            status=422
        except (s.BusinessError,IntegrityError,OperationalError,ValidationError) as exc:status=add_error(form,exc)
    cancel=reverse('detail',args=[kind,pk]) if pk else reverse('record_list',args=[kind])
    return render(request,'form.html',context(request,form=form,heading=f'{"編輯" if pk else "新增"}{s.LABELS[kind]}',kind=kind,cancel=cancel,warnings=warnings),status=status)

@login_required
def action(request,kind,pk,operation):
    if request.method not in ['GET','POST']:return HttpResponseNotAllowed(['GET','POST'])
    if operation not in ACTION_LABELS:raise Http404()
    try:
        model=model_for(request,kind);obj=get_object_or_404(model,pk=pk)
        if kind=='employee':raise Http404()
        s.authorize(request.user,obj if operation!='transfer' else None,'TRANSFER' if operation=='transfer' else None)
        token,rows=s.preview(request.user,kind,pk,operation) if operation in ['deactivate','continue','transfer'] else ('',[])
    except s.BusinessError as exc:return error_page(request,exc)
    form=action_form(operation,obj,rows,token,request.POST if request.method=='POST' else None)
    status=200
    if request.method=='POST':
        try:
            validate_post(request,form)
            if form.is_valid():
                values=dict(form.cleaned_data);command=values.pop('command_id');expected=values.pop('expected_version')
                decisions={name.removeprefix('work_').replace('_',':',1):('transfer' if operation=='transfer' else value) for name,value in values.items() if name.startswith('work_') and (value or operation!='transfer')}
                data={name:value for name,value in values.items() if not name.startswith('work_')}
                if 'new_owner' in data:data['new_owner']=str(data['new_owner'].pk) if data['new_owner'] else None
                if operation in ['deactivate','continue','transfer']:data['decisions']=decisions
                s.execute(request.user,command,operation,kind,pk,expected,data)
                return redirect('detail',kind=kind,pk=pk)
            status=422
        except (s.BusinessError,IntegrityError,OperationalError,ValidationError) as exc:status=add_error(form,exc)
    return render(request,'form.html',context(request,form=form,heading=ACTION_LABELS[operation],description=str(obj),cancel=reverse('detail',args=[kind,pk]),is_action=True,preview_rows=rows,refresh_preview=request.path if operation in ['deactivate','continue','transfer'] else None),status=status)
