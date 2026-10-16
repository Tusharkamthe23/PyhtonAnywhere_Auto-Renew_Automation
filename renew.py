
import os
import sys
import logging
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.pythonanywhere.com"
LOGIN_URL = f"{BASE_URL}/login/"

USERNAME = os.getenv("PA_USERNAME")
PASSWORD = os.getenv("PA_PASSWORD")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def get_soup(response):
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def login(session):
    logging.info("Opening PythonAnywhere login page")

    response = session.get(LOGIN_URL, timeout=30)
    soup = get_soup(response)

    token = soup.find(
        "input", {"name": "csrfmiddlewaretoken"}
    )

    if not token:
        raise RuntimeError("Login CSRF token not found")

    payload = {
        "csrfmiddlewaretoken": token["value"],
        "auth-username": USERNAME,
        "auth-password": PASSWORD,
        "login_view-current_step": "auth",
    }

    response = session.post(
        LOGIN_URL,
        data=payload,
        headers={"Referer": LOGIN_URL},
        timeout=30,
        allow_redirects=True,
    )

    response.raise_for_status()

    soup = get_soup(response)
    page_text = soup.get_text(" ", strip=True).lower()

    if "login" in response.url.lower():
        raise RuntimeError(
            "Login unsuccessful; check credentials or login flow"
        )

    if "log out" not in page_text and "logout" not in page_text:
        raise RuntimeError(
            "Could not confirm login; dashboard marker missing"
        )

    logging.info("Login verified")



def get_expiry(soup, domain):
    pane_id = f"id_{domain.replace('.', '_')}"
    pane = soup.find(id=pane_id)

    if pane is None:
        logging.warning(
            "Dashboard pane not found. Expected id=%s", pane_id
        )
        logging.info(
            "Available dashboard IDs: %s",
            [
                element.get("id")
                for element in soup.find_all(id=True)
                if "webapp" in element.get("id", "").lower()
                or "pythonanywhere" in element.get("id", "").lower()
            ][:20],
        )
        return None

    expiry = pane.find("p", class_="webapp_expiry")

    if expiry:
        text = expiry.get_text(" ", strip=True)
        logging.info("Expiry element found: %s", text[:200])
        return text

    logging.warning(
        "Expiry element not found inside pane %s", pane_id
    )

    logging.info(
        "Paragraphs inside pane: %s",
        [
            p.get_text(" ", strip=True)[:120]
            for p in pane.find_all("p")
        ][:15],
    )

    return None


def renew_webapps(session):
    dashboard_url = (
        f"{BASE_URL}/user/{USERNAME}/webapps/"
    )

    response = session.get(dashboard_url, timeout=30)
    soup = get_soup(response)

    forms = [
        form
        for form in soup.find_all("form", action=True)
        if "/extend" in form["action"].lower()
    ]

    if not forms:
        logging.info(
            "No renewal form found. Renewal may not be due, "
            "or the page structure may have changed."
        )
        return

    for form in forms:
        action = urljoin(BASE_URL, form["action"])

        domain = (
            action.rstrip("/")
            .split("/webapps/")[-1]
            .replace("/extend", "")
        )

        csrf = form.find(
            "input", {"name": "csrfmiddlewaretoken"}
        )

        if not csrf:
            raise RuntimeError(
                f"No CSRF token found for web app {domain}"
            )

        old_expiry = get_expiry(soup, domain)

        logging.info("Checking renewal for %s", domain)

        result = session.post(
            action,
            data={"csrfmiddlewaretoken": csrf["value"]},
            headers={"Referer": dashboard_url},
            timeout=30,
            allow_redirects=True,
        )

        result.raise_for_status()
        logging.info(
            "Renewal response: HTTP %s, final URL: %s",
            result.status_code,
            result.url,
        )

        # Fetch the dashboard again to verify the outcome.
        check_response = session.get(
            dashboard_url, timeout=30
        )
        check_soup = get_soup(check_response)
        new_expiry = get_expiry(check_soup, domain)

        logging.info(
            "Web app: %s | expiry before: %s | after: %s",
            domain,
            old_expiry or "unknown",
            new_expiry or "unknown",
        )

        if old_expiry and new_expiry and old_expiry != new_expiry:
            logging.info("Expiry date changed successfully")
        elif old_expiry == new_expiry and old_expiry:
            logging.warning(
                "Expiry date did not change. Renewal is "
                "unconfirmed; check the dashboard manually."
            )
        else:
            logging.warning(
                "Could not verify the expiry change. "
                "Check the dashboard manually."
            )


def main():
    if not USERNAME or not PASSWORD:
        logging.error(
            "Set PA_USERNAME and PA_PASSWORD environment variables"
        )
        return 1

    session = requests.Session()
    session.headers.update({
        "User-Agent": "PythonAnywhereRenewalChecker/1.0"
    })

    try:
        login(session)
        renew_webapps(session)
        return 0
    except requests.RequestException as exc:
        logging.error("Network or HTTP error: %s", exc)
        return 1
    except RuntimeError as exc:
        logging.error("%s", exc)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
