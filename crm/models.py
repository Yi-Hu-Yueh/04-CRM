import uuid
from django.db import models
from django.db.models import Q
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager

class EmployeeManager(BaseUserManager):
    def create_user(self, login_name, password, **extra):
        from .auth import normalize_login
        user = self.model(login_name=normalize_login(login_name), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

class Employee(AbstractBaseUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    login_name = models.TextField(unique=True)
    display_name = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = EmployeeManager()
    USERNAME_FIELD = 'login_name'
    def __str__(self):
        return self.display_name or self.login_name

class EmployeeGrant(models.Model):
    CAPABILITIES = [('GLOBAL_EDIT', '全域編輯'), ('TRANSFER', '責任轉交'), ('ACCOUNT_ADMIN', '帳號管理')]
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='capabilities')
    capability = models.TextField(choices=CAPABILITIES)
    granted_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['employee', 'capability'], name='unique_capability'),
                       models.CheckConstraint(condition=Q(capability__in=['GLOBAL_EDIT','TRANSFER','ACCOUNT_ADMIN']), name='valid_capability')]

class OperationEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    command_id = models.UUIDField(db_index=True)
    actor = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT)
    happened_at = models.DateTimeField(auto_now_add=True)
    operation = models.TextField()
    target_kind = models.TextField()
    target_id = models.UUIDField(db_index=True)
    before = models.JSONField(default=dict)
    after = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    class Meta:
        ordering = ['happened_at', 'id']

class Core(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_by = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='%(class)s_created')
    owner = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name='%(class)s_owned')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(default=1)
    class Meta:
        abstract = True
    def __str__(self):
        return getattr(self, 'name', '') or getattr(self, 'title', '') or getattr(self, 'subject', '') or f'紀錄 {str(self.id)[:8]}'

class Company(Core):
    name = models.TextField(blank=True)
    phone = models.TextField(blank=True)
    email = models.TextField(blank=True)
    address = models.TextField(blank=True)
    tax_identifier = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    deactivation_event = models.ForeignKey(OperationEvent, null=True, blank=True, on_delete=models.PROTECT, related_name='+')

class Contact(Core):
    name = models.TextField(blank=True)
    phone = models.TextField(blank=True)
    email = models.TextField(blank=True)
    notes = models.TextField(blank=True)

class ContactAffiliation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name='affiliations')
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    created_event = models.ForeignKey(OperationEvent, on_delete=models.PROTECT, related_name='+')
    ended_event = models.ForeignKey(OperationEvent, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['contact'], condition=Q(ended_at__isnull=True), name='one_current_affiliation'),
            models.CheckConstraint(condition=Q(ended_at__isnull=True)|Q(ended_at__gte=models.F('started_at')), name='affiliation_period')]

STAGES = [('NEEDS_CONFIRMATION','需求確認'), ('SOLUTION_DISCUSSION','方案討論'), ('PRICE_NEGOTIATION','議價')]
OPPORTUNITY_STATES = [('ACTIVE','進行中'), ('HELD','暫緩'), ('CANCELLED','已取消'), ('WON','成交'), ('LOST','未成交')]

class Opportunity(Core):
    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name='opportunities')
    title = models.TextField(blank=True)
    need_description = models.TextField(blank=True)
    stage = models.TextField(choices=STAGES, default='NEEDS_CONFIRMATION', db_index=True)
    status = models.TextField(choices=OPPORTUNITY_STATES, default='ACTIVE', db_index=True)
    current_outcome_reason = models.TextField(blank=True)
    current_outcome_at = models.DateTimeField(null=True, blank=True)
    current_outcome_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(stage__in=[s[0] for s in STAGES]), name='opportunity_stage'),
            models.CheckConstraint(condition=Q(status__in=[s[0] for s in OPPORTUNITY_STATES]), name='opportunity_status'),
            models.CheckConstraint(condition=(Q(status__in=['WON','LOST']) & ~Q(current_outcome_reason='') & Q(current_outcome_at__isnull=False,current_outcome_by__isnull=False)) | (Q(status__in=['ACTIVE','HELD','CANCELLED']) & Q(current_outcome_reason='',current_outcome_at__isnull=True,current_outcome_by__isnull=True)),name='outcome_evidence')]

class OpportunityContact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opportunity = models.ForeignKey(Opportunity, on_delete=models.PROTECT, related_name='participations')
    contact = models.ForeignKey(Contact, on_delete=models.PROTECT)
    added_event = models.ForeignKey(OperationEvent, on_delete=models.PROTECT, related_name='+')
    removed_event = models.ForeignKey(OperationEvent, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['opportunity','contact'],condition=Q(removed_event__isnull=True),name='one_current_participation')]

class Enquiry(Core):
    subject = models.TextField(blank=True)
    content = models.TextField(blank=True)
    source_note = models.TextField(blank=True)
    known_contact_name = models.TextField(blank=True)
    known_phone = models.TextField(blank=True)
    known_email = models.TextField(blank=True)
    company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.PROTECT, related_name='enquiries')
    opportunity = models.ForeignKey(Opportunity, null=True, blank=True, on_delete=models.PROTECT, related_name='enquiries')
    status = models.TextField(choices=[('OPEN','未結束'),('CLOSED','已結束')], default='OPEN', db_index=True)
    qualification_note = models.TextField(blank=True)
    qualified_at = models.DateTimeField(null=True, blank=True)
    qualified_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    closure_note = models.TextField(blank=True)
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(status__in=['OPEN','CLOSED']),name='enquiry_status'),
            models.CheckConstraint(condition=Q(status='OPEN',closed_at__isnull=True,closed_by__isnull=True)|Q(status='CLOSED',closed_at__isnull=False,closed_by__isnull=False), name='enquiry_closure_evidence')]

class SubjectCore(Core):
    subject_company = models.ForeignKey(Company, null=True, blank=True, on_delete=models.PROTECT, related_name='%(class)s_subjects')
    subject_enquiry = models.ForeignKey(Enquiry, null=True, blank=True, on_delete=models.PROTECT, related_name='%(class)s_subjects')
    subject_opportunity = models.ForeignKey(Opportunity, null=True, blank=True, on_delete=models.PROTECT, related_name='%(class)s_subjects')
    continuation_grant = models.ForeignKey('ContinuationGrant', null=True, blank=True, on_delete=models.PROTECT, related_name='%(class)s_evidence')
    class Meta:
        abstract = True
        constraints = [models.CheckConstraint(condition=(Q(subject_company__isnull=True,subject_enquiry__isnull=True)|Q(subject_company__isnull=True,subject_opportunity__isnull=True)|Q(subject_enquiry__isnull=True,subject_opportunity__isnull=True)),name='%(class)s_one_subject')]

class Task(SubjectCore):
    title = models.TextField(blank=True)
    description = models.TextField(blank=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    due_on = models.DateField(null=True, blank=True, db_index=True)
    status = models.TextField(choices=[('OPEN','未完成'),('COMPLETED','已完成'),('CANCELLED','已取消')],default='OPEN',db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    finished_by = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    class Meta(SubjectCore.Meta):
        abstract = False
        constraints = SubjectCore.Meta.constraints + [models.CheckConstraint(condition=Q(status__in=['OPEN','COMPLETED','CANCELLED']), name='task_status'),
            models.CheckConstraint(condition=Q(status='OPEN',finished_at__isnull=True,finished_by__isnull=True)|Q(status__in=['COMPLETED','CANCELLED'],finished_at__isnull=False,finished_by__isnull=False),name='task_finish_evidence')]

class Interaction(SubjectCore):
    content = models.TextField(blank=True)
    participant_note = models.TextField(blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    fact_confirmed = models.BooleanField(default=False)
    context_snapshot = models.JSONField(default=dict, blank=True)
    class Meta(SubjectCore.Meta):
        abstract = False
        constraints = SubjectCore.Meta.constraints + [models.CheckConstraint(condition=Q(fact_confirmed=True),name='interaction_actual_fact')]

class ContinuationGrant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.PROTECT)
    deactivation_event = models.ForeignKey(OperationEvent, on_delete=models.PROTECT)
    enquiry = models.ForeignKey(Enquiry, null=True, blank=True, on_delete=models.PROTECT)
    opportunity = models.ForeignKey(Opportunity, null=True, blank=True, on_delete=models.PROTECT)
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.PROTECT)
    confirmed_by = models.ForeignKey(Employee, on_delete=models.PROTECT)
    confirmed_at = models.DateTimeField(auto_now_add=True)
    source_version = models.PositiveIntegerField()
    def __str__(self):
        return f'{self.company}｜{self.enquiry or self.opportunity or self.task}｜確認：{self.confirmed_by}'
    class Meta:
        constraints = [models.CheckConstraint(condition=Q(enquiry__isnull=False,opportunity__isnull=True,task__isnull=True)|Q(enquiry__isnull=True,opportunity__isnull=False,task__isnull=True)|Q(enquiry__isnull=True,opportunity__isnull=True,task__isnull=False),name='grant_one_work')]

class CommandReceipt(models.Model):
    actor = models.ForeignKey(Employee, on_delete=models.PROTECT)
    command_id = models.UUIDField()
    operation = models.TextField()
    request_fingerprint = models.TextField()
    result_kind = models.TextField()
    result_id = models.UUIDField()
    committed_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['actor','command_id'],name='unique_command')]
