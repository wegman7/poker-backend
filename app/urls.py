from django.urls import URLPattern, path, include
from django.views.generic import RedirectView
from rest_framework import routers, serializers, viewsets
from django.contrib import admin

from . import views

urlpatterns: list[URLPattern] = [
    path(route='helloworld/', view=views.HelloWorldView.as_view(), name="change-avatar"),
    path('admin/', admin.site.urls)
]
