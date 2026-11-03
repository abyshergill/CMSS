from django.urls import include, path

urlpatterns = [path("", include("core.urls"))]    # the Django admin site is deliberately NOT enabled (smaller attack surface)
