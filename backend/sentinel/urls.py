from django.contrib.staticfiles.views import serve as static_serve
from django.urls import path, re_path
from alerts import views

urlpatterns = [
    path("", views.dashboard_view, name="dashboard"),
    path("api/metrics/", views.metrics_view, name="metrics"),
]

# Daphne (unlike `runserver`) doesn't auto-serve staticfiles; this is a
# deliberately simple dev-grade static server for this exercise. In a real
# deployment this would be whitenoise/nginx/a CDN instead.
#
# Deliberately NOT using django.contrib.staticfiles.urls.staticfiles_urlpatterns()
# here: that helper wraps django.conf.urls.static.static(), which silently
# returns zero URL patterns when DEBUG=False -- i.e. it works locally
# (docker-compose sets DJANGO_DEBUG=true) and 404s everywhere in a
# DEBUG=false deploy, with no error, just a missing route. Calling the
# view directly skips that gate -- but the view itself has a SECOND,
# independent DEBUG check (`if not settings.DEBUG and not insecure: raise
# Http404`), so `insecure=True` is required too or this 404s exactly the
# same way for exactly the same underlying reason.
urlpatterns += [
    re_path(r"^static/(?P<path>.*)$", static_serve, kwargs={"insecure": True}),
]
