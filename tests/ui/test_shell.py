"""Sign-in, the application shell and the security of the pages."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from tests.api.support import USER_PASSWORD, Api
from tests.ui.conftest import Browser

ANY_PAGE = ("/account", "/teams", "/members", "/skills", "/absence-types")


async def test_the_login_page_is_public_and_well_formed(browser: Browser) -> None:
    response = await browser.page("/login")
    assert response.status_code == 200
    assert 'name="username"' in response.text
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert "rp_login" in response.headers["set-cookie"]


async def test_pages_need_a_session(browser: Browser) -> None:
    response = await browser.page("/teams?q=a")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Fteams%3Fq%3Da"

    ajax = await browser.http.get(
        "/teams", headers={"HX-Request": "true", "HX-Current-URL": "http://test/members"}
    )
    assert ajax.status_code == 401
    assert ajax.headers["hx-redirect"] == "/login?next=%2Fmembers"


async def test_wrong_credentials_say_so_without_giving_a_hint(api: Api, browser: Browser) -> None:
    await api.add_user("ada", "viewer")
    response = await browser.login("ada", "not-the-password")
    assert response.status_code == 401
    assert "The user name or the password is wrong." in response.text
    assert "rp_session" not in response.headers.get("set-cookie", "")


async def test_login_without_the_form_token_is_refused(api: Api, browser: Browser) -> None:
    await api.add_user("ada", "viewer")
    response = await browser.http.post(
        "/login", data={"username": "ada", "password": USER_PASSWORD}
    )
    assert response.status_code == 403
    assert "rp_session" not in response.headers.get("set-cookie", "")


async def test_signing_in_lands_on_a_page_the_user_may_open(api: Api, browser: Browser) -> None:
    await api.add_user("ada", "viewer")
    response = await browser.login("ada")
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "rp_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert (await browser.page("/")).headers["location"] == "/planning"
    await api.add_user("pat", permissions=["member:read"])
    narrow = Browser(browser.http._transport.app)  # type: ignore[attr-defined]
    try:
        await narrow.sign_in("pat")
        assert (await narrow.page("/")).headers["location"] == "/members"
    finally:
        await narrow.close()


async def test_open_redirects_are_not_followed(api: Api, browser: Browser) -> None:
    await api.add_user("ada", "viewer")
    page = await browser.http.get("/login", params={"next": "//evil.example/x"})
    assert 'name="next" value="/"' in page.text
    page = await browser.http.get("/login", params={"next": "https://evil.example"})
    assert 'name="next" value="/"' in page.text


async def test_the_shell_shows_only_what_the_role_allows(api: Api, make_browser: Any) -> None:
    await api.add_user("vera", "viewer")
    await api.add_user("pat", permissions=["team:read"])
    viewer, narrow = make_browser(), make_browser()
    await viewer.sign_in("vera")
    await narrow.sign_in("pat")
    full = (await viewer.page("/teams")).text
    assert 'href="/members"' in full and 'href="/skills"' in full
    assert "New team" not in full  # read-only role
    few = (await narrow.page("/teams")).text
    assert 'href="/teams"' in few and 'href="/members"' not in few
    assert (await narrow.page("/members")).status_code == 403


async def test_a_pending_password_change_blocks_everything_else(api: Api, browser: Browser) -> None:
    await api.add_user("new", "viewer", must_change_password=True)
    login = await browser.login("new")
    assert login.headers["location"] == "/change-password"
    blocked = await browser.page("/teams")
    assert blocked.headers["location"] == "/change-password"
    form = await browser.page("/change-password")
    assert form.status_code == 200 and "Choose a new password" in form.text

    mismatch = await browser.http.post(
        "/change-password",
        data={
            "csrf_token": browser.csrf,
            "current_password": USER_PASSWORD,
            "new_password": "Fresh-Horse-Staple-42",
            "confirm_password": "different",
        },
    )
    assert mismatch.status_code == 422 and "not the same" in mismatch.text
    weak = await browser.http.post(
        "/change-password",
        data={
            "csrf_token": browser.csrf,
            "current_password": USER_PASSWORD,
            "new_password": "short",
            "confirm_password": "short",
        },
    )
    assert weak.status_code == 422
    done = await browser.http.post(
        "/change-password",
        data={
            "csrf_token": browser.csrf,
            "current_password": USER_PASSWORD,
            "new_password": "Fresh-Horse-Staple-42",
            "confirm_password": "Fresh-Horse-Staple-42",
        },
    )
    assert done.status_code == 303, done.text
    assert done.headers["location"] == "/"
    assert (await browser.page("/teams")).status_code == 200


async def test_signing_out_ends_the_session(api: Api, browser: Browser) -> None:
    await api.add_user("ada", "viewer")
    await browser.sign_in("ada")
    out = await browser.http.post("/logout", data={"csrf_token": browser.csrf})
    assert out.status_code == 303 and out.headers["location"] == "/login"
    assert (await browser.page("/teams")).status_code == 303


async def test_the_page_signs_out_by_navigating_itself(api: Api, browser: Browser) -> None:
    """A redirect after a form post may not leave the site (CSP), so the page does it itself."""
    await api.add_user("ira", "viewer")
    await browser.sign_in("ira")
    page = await browser.page("/teams")
    assert 'hx-post="/logout"' in page.text
    out = await browser.http.post(
        "/logout", data={"csrf_token": browser.csrf}, headers={"HX-Request": "true"}
    )
    assert out.status_code == 204 and out.headers["hx-redirect"] == "/login"
    assert (await browser.page("/teams")).status_code == 303


async def test_changes_need_the_csrf_token_of_the_session(api: Api, browser: Browser) -> None:
    await api.add_user("zoe", "admin")
    await browser.sign_in("zoe")
    refused = await browser.http.post(
        "/teams", data={"name": "A", "color": "#336699"}, headers={"HX-Request": "true"}
    )
    assert refused.status_code == 403
    assert "toast" in refused.headers["hx-trigger"]
    wrong = await browser.http.post(
        "/teams",
        data={"name": "A", "color": "#336699"},
        headers={"HX-Request": "true", "X-CSRF-Token": "x" * 40},
    )
    assert wrong.status_code == 403
    assert (await browser.send("GET", "/teams")).status_code == 200


async def test_two_users_at_the_same_time_never_see_each_other(api: Api, make_browser: Any) -> None:
    await api.add_user("viv", "viewer")
    await api.add_user("wil", "admin")
    viv, wil = make_browser(), make_browser()
    await asyncio.gather(viv.sign_in("viv"), wil.sign_in("wil"))

    async def who(browser: Browser) -> str:
        text = (await browser.page("/account")).text
        match = re.search(r"User name</dt><dd>(\w+)</dd>", text)
        assert match, text
        return match[1]

    names = await asyncio.gather(*[who(viv if i % 2 else wil) for i in range(20)])
    assert names == ["wil", "viv"] * 10


async def test_no_page_runs_inline_script_or_handlers(api: Api, browser: Browser) -> None:
    """The policy forbids inline script; the markup must not depend on it."""
    await api.add_user("zoe", "admin")
    await browser.sign_in("zoe")
    pages = [await browser.page(url) for url in (*ANY_PAGE, "/members/new")]
    pages.append(await browser.http.get("/login"))
    for response in pages:
        html = response.text
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), response.url
        assert not re.search(r"\son[a-z]+\s*=", html), response.url
        assert "javascript:" not in html, response.url
        assert not re.search(r"<style", html), response.url
        assert "unsafe-eval" not in response.headers["content-security-policy"]


async def test_static_files_are_served_with_the_policy(browser: Browser) -> None:
    for path in ("/static/js/app.js", "/static/vendor/htmx.min.js", "/static/favicon.svg"):
        response = await browser.http.get(path)
        assert response.status_code == 200, path
        assert response.headers["x-content-type-options"] == "nosniff"
