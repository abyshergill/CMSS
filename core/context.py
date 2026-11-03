from .models import Profile
from .permissions import is_admin, is_full_admin, role_name, role_of
from .views._util import PAGE_SIZES


def nav(request):
    u = request.user
    return {"admin_user": is_admin(u), "full_admin": is_full_admin(u), "role": role_of(u), "role_label": role_name(u),
            "page_sizes": PAGE_SIZES}
