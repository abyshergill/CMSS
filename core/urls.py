from django.urls import path

from .views import accounts, equipment, projects, reports, setup, work

urlpatterns = [
    path("", work.today_view, name="today"),
    path("calendar/", work.calendar_view, name="calendar"),
    path("insights/", reports.insights_view, name="insights"),

    path("jobs/", work.jobs_list, name="jobs"),
    path("jobs/new/", work.job_new, name="job_new"),
    path("jobs/<int:pk>/", work.job_detail, name="job_detail"),
    path("jobs/<int:pk>/edit/", work.job_edit, name="job_edit"),
    path("jobs/<int:pk>/toggle/", work.job_toggle, name="job_toggle"),
    path("jobs/<int:pk>/delete/", work.job_delete, name="job_delete"),
    path("jobs/<int:pk>/files/", work.job_upload, name="job_upload"),
    path("files/job/<int:pk>/", work.attachment_download, name="attachment"),
    path("files/job/<int:pk>/delete/", work.attachment_delete, name="attachment_delete"),
    path("files/done/<int:pk>/", work.evidence_download, name="evidence"),

    path("exceptions/skip/", work.skip_create, name="skip_create"),
    path("exceptions/skip/<int:pk>/undo/", work.skip_undo, name="skip_undo"),
    path("exceptions/move/", work.move_create, name="move_create"),
    path("exceptions/move/<int:pk>/undo/", work.move_undo, name="move_undo"),
    path("done/", work.done, name="done"),
    path("completions/<int:pk>/undo/", work.completion_undo, name="completion_undo"),

    path("projects/", projects.project_list, name="projects"),
    path("projects/new/", projects.project_new, name="project_new"),
    path("projects/<int:pk>/", projects.project_detail, name="project_detail"),
    path("projects/<int:pk>/edit/", projects.project_edit, name="project_edit"),
    path("projects/<int:pk>/toggle/", projects.project_toggle, name="project_toggle"),
    path("projects/<int:pk>/delete/", projects.project_delete, name="project_delete"),
    path("projects/<int:pk>/tasks/new/", projects.task_new, name="task_new"),

    path("equipment/", equipment.equipment_list, name="equipment"),
    path("equipment/new/", equipment.equipment_new, name="equipment_new"),
    path("equipment/<int:pk>/", equipment.equipment_detail, name="equipment_detail"),
    path("equipment/<int:pk>/edit/", equipment.equipment_edit, name="equipment_edit"),
    path("equipment/<int:pk>/archive/", equipment.equipment_archive, name="equipment_archive"),

    path("history/", reports.history_view, name="history"),
    path("history/export/", reports.export_view, name="export"),

    path("setup/", setup.setup_view, name="setup"),
    path("setup/area/add/", setup.area_add, name="area_add"),
    path("setup/area/<int:pk>/delete/", setup.area_delete, name="area_delete"),
    path("setup/user/add/", setup.user_add, name="user_add"),
    path("setup/user/<int:pk>/toggle/", setup.user_toggle, name="user_toggle"),
    path("setup/user/<int:pk>/password/", setup.user_password, name="user_password"),
    path("setup/user/<int:pk>/role/", setup.user_role, name="user_role"),
    path("setup/group/add/", setup.group_add, name="group_add"),
    path("setup/group/<int:pk>/", setup.group_detail, name="group_detail"),
    path("setup/group/<int:pk>/edit/", setup.group_edit, name="group_edit"),
    path("setup/group/<int:pk>/members/add/", setup.group_members_add, name="group_members_add"),
    path("setup/group/<int:pk>/members/<int:uid>/remove/", setup.group_member_remove, name="group_member_remove"),
    path("setup/group/<int:pk>/delete/", setup.group_delete, name="group_delete"),

    path("login/", accounts.login_view, name="login"),
    path("logout/", accounts.logout_view, name="logout"),
    path("first-run/", accounts.first_run, name="first_run"),
    path("account/password/", accounts.PasswordChange.as_view(), name="password_change"),
]
