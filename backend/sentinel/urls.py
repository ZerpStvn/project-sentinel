from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import path
from alerts import views

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("api/metrics/", views.metrics_view, name="metrics"),
]

# Daphne (unlike `runserver`) doesn't auto-serve staticfiles; this is a
# deliberately simple dev-grade static server for this exercise. In a real
# deployment this would be whitenoise/nginx/a CDN instead.
urlpatterns += staticfiles_urlpatterns()
