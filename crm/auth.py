import unicodedata
from django.contrib.auth.backends import ModelBackend

def normalize_login(value):
    return unicodedata.normalize('NFKC', value).strip().casefold()

class EmployeeBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        return super().authenticate(request, username=normalize_login(username or kwargs.get('login_name', '')), password=password)
