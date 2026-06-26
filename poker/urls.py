from django.urls import path
from . import views

urlpatterns = [
    path('health/<str:room_id>/', views.room_health),
]
