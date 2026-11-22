import getpass
import uuid
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from crm.models import Employee,EmployeeGrant,OperationEvent

class Command(BaseCommand):
    help='在空白資料庫互動建立第一位具三項專項授權的管理員（不建立示範業務資料）'
    def handle(self,*args,**options):
        if Employee.objects.exists():raise CommandError('已有員工帳號，請由具帳號管理授權的人員在系統內管理。')
        name=input('第一位員工的登入帳號：').strip()
        password=getpass.getpass('設定密碼：')
        confirm=getpass.getpass('再輸入一次密碼：')
        if not name or not password or password!=confirm:raise CommandError('帳號不可空白，兩次密碼須相同且不可空白。')
        with transaction.atomic():
            if Employee.objects.exists():raise CommandError('已有員工帳號，取消初始化。')
            employee=Employee.objects.create_user(name,password)
            for capability,_ in EmployeeGrant.CAPABILITIES:EmployeeGrant.objects.create(employee=employee,capability=capability,granted_by=employee)
            OperationEvent.objects.create(command_id=uuid.uuid4(),actor=employee,operation='bootstrap',target_kind='employee',target_id=employee.pk,after={'login_name':employee.login_name,'capabilities':[c[0] for c in EmployeeGrant.CAPABILITIES]},reason='Owner 在本機互動初始化第一位管理員')
        self.stdout.write(self.style.SUCCESS('帳號已建立，請使用個人帳號登入。'))
