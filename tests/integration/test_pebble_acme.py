"""End-to-end ACME DNS-01 issuance against Pebble (Let's Encrypt's test server).

Runs only when a Pebble environment is available (CI starts one):

    PEBBLE_DIRECTORY=https://localhost:14000/dir
    ACME_CA_BUNDLE=/path/to/pebble.minica.pem      # trusts Pebble's API certificate
    ACME_TEST_DNS_URL=http://localhost:8055        # pebble-challtestsrv management API

Pebble validates the TXT records through pebble-challtestsrv, so this exercises the real
protocol: account, order, DNS-01 challenge, validation, finalization and download.
"""
import os

import pytest

from app import audit, certificates, dns_providers

pytestmark = pytest.mark.skipif(not (os.environ.get("PEBBLE_DIRECTORY") and os.environ.get("ACME_TEST_DNS_URL")),
                                reason="Pebble test environment not available")


def configure(**extra):
    certificates.save_config({
        "hostnames": ["pager.school-example.com", "*.bells.school-example.com"], "provider": "pebble_challtestsrv",
        "ca": "custom", "directory_url": os.environ["PEBBLE_DIRECTORY"], "agree_tos": True, "enabled": True,
        "email": "it@school-example.com", **extra})


def test_issue_and_renew_certificate_with_pebble(env):
    configure()
    certificates.request_certificate("ci", wait=True)
    job = certificates.current_job()
    assert job["ok"], job["error"]
    cert = certificates.get_state()["certificate"]
    assert sorted(cert["names"]) == ["*.bells.school-example.com", "pager.school-example.com"]
    assert "Pebble" in cert["issuer"]
    texts = [s["text"] for s in job["steps"]]
    assert "Removing the temporary DNS records" in texts and "Downloading the certificate" in texts
    status = certificates.status({"running": False, "port": 8443, "error": ""})
    assert status["certificate"]["covers_hostnames"]

    # a second request (renewal) reuses the ACME account and replaces the certificate
    first_serial = cert["serial"]
    accounts_before = dict(certificates.get_state()["accounts"])
    certificates.request_certificate("system", reason="renewal", wait=True)
    assert certificates.current_job()["ok"]
    assert certificates.get_state()["certificate"]["serial"] != first_serial
    assert certificates.get_state()["accounts"].keys() == accounts_before.keys()
    assert len(audit.search(action="https.certificate_issued")["items"]) == 2


def test_wrong_dns_record_fails_validation_and_cleans_up(env):
    configure(hostnames=["wrong.school-example.com"])
    removed = []
    real = dns_providers.ChallTestSrv(os.environ["ACME_TEST_DNS_URL"])

    class WrongValue(dns_providers.ChallTestSrv):
        def add_txt(self, fqdn, value):
            return real.add_txt(fqdn, "not-the-right-value")

        def remove_txt(self, rec):
            removed.append(rec.fqdn)
            real.remove_txt(rec)

    certificates.set_provider_factory(lambda cfg: WrongValue(os.environ["ACME_TEST_DNS_URL"]))
    try:
        certificates.request_certificate("ci", wait=True)
    finally:
        certificates.set_provider_factory(None)
    job = certificates.current_job()
    assert job["ok"] is False and job["error"]["code"] in ("validation_failed", "acme_error"), job["error"]
    assert removed == ["_acme-challenge.wrong.school-example.com"]
    assert "certificate" not in certificates.get_state()
    assert certificates.get_state()["failures"] == 1
