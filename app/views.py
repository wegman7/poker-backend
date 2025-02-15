from django.http import HttpRequest, JsonResponse
from rest_framework import generics, status, permissions
from rest_framework.response import Response
from django.contrib.auth.models import User

from .permissions import ReadMessagesPermission, ExamplePermission

class HelloWorldView(generics.GenericAPIView):

    permission_classes = (permissions.IsAuthenticated, ReadMessagesPermission)

    def get(self, request: HttpRequest):
        return Response(status=status.HTTP_200_OK)