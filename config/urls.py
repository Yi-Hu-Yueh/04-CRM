from django.urls import path
from django.contrib.staticfiles.views import serve
from crm import views

urlpatterns = [
    path('login/', views.sign_in, name='login'),
    path('logout/', views.sign_out, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('system/export/', views.export_system, name='export_system'),
    path('records/<str:kind>/', views.record_list, name='record_list'),
    path('records/<str:kind>/new/', views.edit, name='new'),
    path('records/<str:kind>/<uuid:pk>/', views.detail, name='detail'),
    path('records/<str:kind>/<uuid:pk>/edit/', views.edit, name='edit'),
    path('records/<str:kind>/<uuid:pk>/action/<str:operation>/', views.action, name='action'),
    path('static/<path:path>', serve, {'insecure': True}),
]
