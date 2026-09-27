"""Who is using the site. There is no sign-in yet (an internal tool on the lab network):
people type a name, kept in a cookie, which is recorded as the owner of what they do.

Everything asks currentUser() and nothing else, so adding local accounts (and later SSO)
replaces this module's body and adds a login page; callers do not change.
"""
import re

COOKIE = "ambuild_owner"
_NAME = re.compile(r"[^\w .@'-]", re.UNICODE)


def cleanName(name):
    """A display name: printable, at most 64 characters"""
    return _NAME.sub("", (name or "").strip())[:64]


def currentUser(request):
    """The name of the person making this request, or None if they have not given one"""
    return cleanName(request.cookies.get(COOKIE)) or None
