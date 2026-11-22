from django.core.management.base import BaseCommand
from crm.models import Employee

class Command(BaseCommand):
    help='回傳 10 表示尚無員工；供本機啟動程式使用'
    def handle(self,*args,**options):
        if not Employee.objects.exists():raise SystemExit(10)
