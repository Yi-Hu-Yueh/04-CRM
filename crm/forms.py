import uuid
from django import forms
from django.forms import modelform_factory
from . import models as m
from .services import FIELDS,MODELS

class RecordModelForm(forms.ModelForm):
    def clean(self):
        data=super().clean()
        subjects=[k for k in ['subject_company','subject_enquiry','subject_opportunity'] if data.get(k)]
        if len(subjects)>1:
            self.add_error(subjects[0],'主要業務主題最多選擇一個，請只保留公司、詢問或商機其中一項。')
        if isinstance(self.instance,m.Interaction) and not data.get('fact_confirmed'):
            self.add_error('fact_confirmed','請明確勾選確認此互動已實際發生。')
        return data

ZH = {
 'name':'名稱','phone':'電話','email':'電郵','address':'地址','tax_identifier':'統一編號','notes':'備註',
 'subject':'詢問主旨','content':'內容','source_note':'來源說明','known_contact_name':'已知聯絡人','known_phone':'已知電話','known_email':'已知電郵',
 'company':'直接公司','opportunity':'連結商機','qualification_note':'資格確認說明','title':'主旨','need_description':'需求說明','stage':'銷售階段',
 'participant_note':'實際參與人員說明','occurred_at':'實際發生時間（可留白）','fact_confirmed':'確認此互動已實際發生',
 'subject_company':'主要主題：公司','subject_enquiry':'主要主題：詢問','subject_opportunity':'主要主題：商機',
 'continuation_grant':'停用公司續辦依據','description':'說明','scheduled_at':'安排時間','due_on':'到期日（可留白）',
 'login_name':'登入帳號','display_name':'顯示名稱','is_active':'帳號啟用',
 'status':'目前狀態','created_by':'建立者','owner':'目前負責人','created_at':'建立時間','updated_at':'最近修改',
 'current_outcome_reason':'本次結果原因','current_outcome_at':'結果確認時間','current_outcome_by':'結果確認人員',
 'qualified_at':'資格確認時間','qualified_by':'資格確認人員','closed_at':'結束時間','closed_by':'結束人員','closure_note':'結束說明',
 'finished_at':'完成／取消時間','finished_by':'完成／取消人員','version':'版本','id':'識別',
}

def record_form(kind,data=None,instance=None):
    labels={k:ZH.get(k,k) for k in FIELDS[kind]}
    if kind=='opportunity': labels['company']='主要公司（必選）'
    widgets={name:forms.Textarea(attrs={'rows':3}) for name in FIELDS[kind] if name in ['content','notes','description','need_description','participant_note','source_note','qualification_note']}
    for name in ['scheduled_at','occurred_at']:
        widgets[name]=forms.DateTimeInput(format='%Y-%m-%dT%H:%M',attrs={'type':'datetime-local'})
    widgets['due_on']=forms.DateInput(attrs={'type':'date'})
    form_cls=modelform_factory(MODELS[kind],form=RecordModelForm,fields=FIELDS[kind],labels=labels,widgets=widgets)
    form=form_cls(data=data,instance=instance,label_suffix='')
    if kind=='opportunity' and instance and instance.status!='ACTIVE':
        form.fields['stage'].disabled=True
        form.fields['stage'].help_text='請先透過恢復進行或重開商機，再調整階段。'
    form.fields['command_id']=forms.UUIDField(initial=uuid.uuid4,widget=forms.HiddenInput())
    form.fields['expected_version']=forms.IntegerField(initial=instance.version if instance else None,required=bool(instance),widget=forms.HiddenInput())
    if kind=='company': form.fields['ack_duplicates']=forms.BooleanField(label='已查看疑似重複警告，仍要保留為獨立公司',required=False)
    if kind=='contact':
        initial=instance.affiliations.filter(ended_at=None).values_list('company_id',flat=True).first() if instance else None
        form.fields['company_affiliation']=forms.ModelChoiceField(label='目前隸屬公司（可無）',queryset=m.Company.objects.all(),required=False,initial=initial)
    if kind=='opportunity':
        initial=instance.participations.filter(removed_event=None).values_list('contact_id',flat=True) if instance else []
        form.fields['participants']=forms.ModelMultipleChoiceField(label='參與聯絡人（可多選或無；按 Ctrl 多選）',queryset=m.Contact.objects.all(),required=False,initial=initial)
    if kind=='employee':
        form.fields['password']=forms.CharField(label='設定密碼（修改時留白表示保留）',required=not bool(instance),widget=forms.PasswordInput(render_value=False))
        form.fields['capabilities']=forms.MultipleChoiceField(label='個別特殊授權',choices=m.EmployeeGrant.CAPABILITIES,required=False,widget=forms.CheckboxSelectMultiple(),initial=list(instance.capabilities.values_list('capability',flat=True)) if instance else [])
    return form

ACTION_LABELS={'qualify':'確認資格','close':'結束詢問','hold':'暫緩','resume':'恢復進行','cancel':'取消工作','won':'確認成交','lost':'確認未成交','reopen':'重開商機',
               'complete':'完成待辦','deactivate':'停用公司','reactivate':'啟用公司','continue':'確認續辦','transfer':'責任轉交'}

def action_form(op,obj,rows,token,data=None):
    form=forms.Form(data=data,label_suffix='')
    form.fields['command_id']=forms.UUIDField(initial=uuid.uuid4,widget=forms.HiddenInput())
    form.fields['expected_version']=forms.IntegerField(initial=obj.version,widget=forms.HiddenInput())
    if op in ['won','lost','qualify','close','cancel']:
        form.fields['reason']=forms.CharField(label='原因／說明',required=op in ['won','lost'],widget=forms.Textarea(attrs={'rows':3}))
    if op in ['resume','reopen']:
        form.fields['stage']=forms.ChoiceField(label='恢復後階段',choices=m.STAGES,initial=obj.stage)
    if op in ['transfer','deactivate','continue']:
        form.fields['preview']=forms.CharField(initial=token,widget=forms.HiddenInput())
        if op=='transfer': form.fields['new_owner']=forms.ModelChoiceField(label='新負責人（留白表示目前無人負責）',queryset=m.Employee.objects.filter(is_active=True),required=False)
        for index,(kind,work) in enumerate(rows):
            if op!='transfer' and kind not in ['task','enquiry','opportunity']: continue
            name=f'work_{kind}_{work.pk}'
            label=f'{kind_label(kind)}｜{work}｜{work.get_status_display() if hasattr(work,"get_status_display") else "公司"}｜負責：{work.owner or "無"}'
            if op=='transfer': form.fields[name]=forms.BooleanField(label=label,required=False,initial=(work.pk==obj.pk))
            else:
                prior=m.ContinuationGrant.objects.filter(company=obj,deactivation_event_id=obj.deactivation_event_id,**{kind:work}).exists() if obj.deactivation_event_id else False
                choices=[('continue','確認續辦')] if prior else [('', '請明確選擇'),('continue','確認續辦'),('freeze','保留，尚未確認續辦')]
                form.fields[name]=forms.ChoiceField(label=label+('（先前已確認續辦）' if prior else ''),choices=choices,initial='continue' if prior else '')
    return form

def kind_label(kind):
    from .services import LABELS
    return LABELS[kind]
