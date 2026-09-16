from django.contrib.staticfiles.views import serve as static_serve
from django.urls import path, re_path
from alerts import views

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("api/metrics/", views.metrics_view, name="metrics"),
]

urlpatterns += [
    re_path(r"^static/(?P<path>.*)$", static_serve, kwargs={"insecure": True}),
]
