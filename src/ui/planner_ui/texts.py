"""Texts of the API's problem codes and field errors, in the language of the page.

The API answers in English with a stable ``code``; the interface shows its own sentence for every
code it knows and falls back to the API's text for the rest. A test checks that each entry is
translated.
"""

from __future__ import annotations

import re

from planner_ui.i18n import N_, gettext_

PROBLEMS: dict[str, str] = {
    "AUTH_REQUIRED": N_("Please sign in."),
    "INVALID_CREDENTIALS": N_("The user name or the password is wrong."),
    "ACCOUNT_LOCKED": N_("The account is locked for a while after too many failed attempts."),
    "ACCOUNT_DISABLED": N_("The account is disabled."),
    "PERMISSION_DENIED": N_("You do not have the permission for this."),
    "CSRF_FAILED": N_("The page is out of date. Reload it and try again."),
    "PASSWORD_CHANGE_REQUIRED": N_("A new password is required before you can continue."),
    "PASSWORD_POLICY": N_("The new password does not meet the requirements."),
    "AUTH_MODE_DISABLED": N_("This way of signing in is switched off."),
    "NOT_FOUND": N_("This entry does not exist any more."),
    "VALIDATION_ERROR": N_("Please check the highlighted fields."),
    "VERSION_MISMATCH": N_("Somebody else changed this entry in the meantime."),
    "VERSION_REQUIRED": N_("The version of the entry is missing. Reload the page."),
    "DUPLICATE": N_("An entry with this name or key already exists."),
    "DELETE_BLOCKED": N_("This entry is still in use and cannot be deleted."),
    "ASSIGNMENT_SKILL_MISMATCH": N_("The member does not have the skill of this assignment."),
    "ASSIGNMENT_SKILL_NOT_IN_TASK": N_("The task does not need this skill."),
    "MEMBER_INACTIVE": N_("Inactive members cannot be planned."),
    "TASK_CLOSED": N_("The task is closed. Reopen it to plan more work."),
    "EFFORT_BELOW_ASSIGNED": N_("The effort is lower than the work already planned."),
    "LAST_ADMIN": N_(
        "This would leave no active local account that can manage users. "
        "Give the right to another account first."
    ),
    "OWN_ACCOUNT": N_("You cannot delete the account you are signed in with."),
    "BUILTIN_ROLE": N_(
        "A built-in role cannot be changed or deleted. Create a custom role instead."
    ),
    "FIELD_LOCKED": N_("This field is maintained by the connected system."),
    "MANAGED_BY_SOURCE": N_("This entry is maintained by the connected system."),
    "WEEK_NOT_MONDAY": N_("A week starts on a Monday."),
    "IMPORT_FAILED": N_("The import failed."),
    "PROVIDER_ERROR": N_("The connected system reported an error."),
    "PROVIDER_UNKNOWN": N_("This integration type is not installed."),
    "SYNC_RUNNING": N_("A synchronisation is already running."),
    "DEMO_SEED_DISABLED": N_("Demo data is switched off on this installation."),
    "DEMO_SEED_NOT_EMPTY": N_("Demo data can only be loaded into an empty installation."),
    "INTERNAL_ERROR": N_("Something went wrong on the server. Please try again."),
    "UNREACHABLE": N_("The service cannot be reached right now. Please try again in a moment."),
}

# Why the identity provider sent a browser back to the sign-in page (``/login?error=<reason>``).
LOGIN_ERRORS: dict[str, str] = {
    "oidc_failed": N_("Single sign-on did not work. Try again, or ask the administrator."),
    "oidc_unavailable": N_("Single sign-on is not available right now. Try again in a moment."),
    "account_disabled": N_("The account is disabled."),
}

FIELD_ERRORS: dict[str, str] = {
    "duplicate": N_("Already in use."),
    "missing": N_("Required."),
    "required": N_("Required."),
    "wrong": N_("Wrong."),
    "locked": N_("Maintained by the connected system."),
    "string_too_short": N_("Too short."),
    "string_too_long": N_("Too long."),
    "string_pattern_mismatch": N_("Not in the expected format."),
    "int_parsing": N_("Not a whole number."),
    "greater_than_equal": N_("Too small."),
    "less_than_equal": N_("Too large."),
}


POLICY_MESSAGES: dict[str, str] = {
    "The password must not contain the user name.": N_(
        "The password must not contain the user name."
    ),
    "The password must not consist of digits only.": N_(
        "The password must not consist of digits only."
    ),
}
_AT_LEAST = re.compile(r"^The password needs at least (\d+) characters\.$")


def policy_text(message: str) -> str:
    """The password rules of the API, in the language of the page."""
    found = _AT_LEAST.match(message)
    if found:
        return gettext_("The password needs at least %(count)s characters.") % {"count": found[1]}
    known = POLICY_MESSAGES.get(message)
    return gettext_(known) if known else message


def problem_text(code: str, fallback: str) -> str:
    text = PROBLEMS.get(code)
    return gettext_(text) if text else fallback


def login_error_text(reason: str | None) -> str | None:
    """The sentence for a known reason; anything else from the address is ignored."""
    text = LOGIN_ERRORS.get(reason or "")
    return gettext_(text) if text else None


def field_error_text(code: str, fallback: str) -> str:
    if code == "policy":
        return policy_text(fallback)
    text = FIELD_ERRORS.get(code)
    return gettext_(text) if text else fallback
