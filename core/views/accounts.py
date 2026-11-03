from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.views import PasswordChangeView
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from ..forms import FirstRunForm, LoginForm
from ..models import Profile
from ..security import client_ip, lock_state, record_attempt, safe_redirect_target

User = get_user_model()


@never_cache
def login_view(request):
    if not User.objects.exists():
        return redirect("first_run")
    if request.user.is_authenticated:
        return redirect("today")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        username, ip = form.cleaned_data["username"].strip(), client_ip(request)
        locked, wait = lock_state(username, ip)
        if locked:
            mins = max(1, -(-wait // 60))
            messages.error(request, f"Too many failed attempts. Please try again in about {mins} minute{'s' if mins != 1 else ''}.")
            return render(request, "login.html", {"form": LoginForm()}, status=429)
        user = authenticate(request, username=username, password=form.cleaned_data["password"])
        record_attempt(username, ip, success=user is not None)
        if user is not None:
            login(request, user)                       # also rotates the session key (no session fixation)
            return redirect(safe_redirect_target(request, request.POST.get("next") or request.GET.get("next"), reverse("today")))
        messages.error(request, "Wrong username or password.")        # same message whether or not the user exists
        form = LoginForm()
    return render(request, "login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("login")


def first_run(request):
    if User.objects.exists():
        return redirect("login")
    form = FirstRunForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        u = User.objects.create_superuser(form.cleaned_data["username"].strip(), password=form.cleaned_data["password1"])
        Profile.objects.update_or_create(user=u, defaults={"role": Profile.ADMIN})
        messages.success(request, "Admin account created. Please sign in.")
        return redirect("login")
    return render(request, "first_run.html", {"form": form})


class PasswordChange(PasswordChangeView):
    template_name = "account_password.html"
    success_url = reverse_lazy("today")
