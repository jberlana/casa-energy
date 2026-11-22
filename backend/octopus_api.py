#!/usr/bin/env python3
"""Read-only Octopus Spain experiment. Only the authentication mutation is permitted."""
from __future__ import annotations

import argparse
import base64
from datetime import date
import getpass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ENDPOINT = "https://api.oees-kraken.energy/v1/graphql/"
ROOT = Path.home() / ".config/octopus-experiment"
MAX_RESPONSE = 4 * 1024 * 1024
OPERATIONS = {
    "authenticate": """mutation CasaAuthenticate($input: ObtainJSONWebTokenInput!) {
      obtainKrakenToken(input: $input) { token refreshToken refreshExpiresIn }
    }""",
    "accounts": """query CasaAccounts { viewer { accounts { number } } }""",
    "overview": """query CasaOverview($account: String!) {
      account(accountNumber: $account) {
        number ledgers(includeDebtLedgers: false) { number name ledgerType }
        properties { id gasSupplyPoints { id status } }
      }
    }""",
    "bills": """query CasaBills($account: String!, $ledger: String, $after: String) {
      account(accountNumber: $account) {
        bills(first: 20, after: $after, ledgerNumber: $ledger) {
          pageInfo { hasNextPage endCursor }
          edges { node {
            id billType fromDate toDate issuedDate
            ... on InvoiceType { identifier grossAmount isAnnulled }
          } }
        }
      }
    }""",
    "document": """query CasaDocument($account: String!, $bill: ID!) {
      account(accountNumber: $account) { bill(id: $bill, billType: INVOICE) {
        id attachments(first: 5) { edges { node { filename temporaryUrl temporaryUrlExpiresAt } } }
      } }
    }""",
    "invoice": """query CasaInvoice($account: String!, $bill: ID!, $after: String) {
      account(accountNumber: $account) { bill(id: $bill, billType: INVOICE) {
        ... on InvoiceType {
          identifier grossAmount isAnnulled
          totalCharges { netTotal taxTotal grossTotal }
          transactions(first: 10, after: $after) {
            pageInfo { hasNextPage endCursor }
            edges { node {
              __typename id title amounts { net tax gross }
              ... on BillCharge { consumption { startDate endDate quantity unit usageCost supplyCharge } }
            } }
          }
        }
      } }
    }""",
    "measurements": """query CasaGasMeasurements(
      $property: ID!, $supply: String!, $start: Date!, $end: Date!, $after: String
    ) {
      property(id: $property) {
        measurements(startOn: $start, endOn: $end, timezone: "Europe/Madrid",
          utilityFilters: [{gasFilters: {marketSupplyPointId: $supply}}], first: 50, after: $after) {
          pageInfo { hasNextPage endCursor }
          edges { node {
            source value unit readAt
            ... on IntervalMeasurementType { startAt endAt durationInSeconds accumulation }
            metaData {
              utilityFilters { ... on GasFiltersOutput { readingFrequencyType marketSupplyPointId } }
              extras { label description value }
            }
          } }
        }
      }
    }""",
}


class APIError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise APIError("Redirect refused; credentials were not forwarded")


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".octopus-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def expiry(token):
    try:
        body = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))["exp"])
    except (ValueError, KeyError, IndexError, TypeError):
        # This is only a refresh hint; token validation remains the provider's responsibility.
        return time.time() + 240


class Client:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.session_path = self.root / "session.json"
        self.opener = urllib.request.build_opener(NoRedirect())

    def _request(self, operation, variables=None, token=None):
        if operation not in OPERATIONS:
            raise APIError("Operation is not in the read-only allowlist")
        payload = {"query": OPERATIONS[operation], "variables": variables or {}}
        headers = {"Content-Type": "application/json", "User-Agent": "CasaLocal/0.1"}
        if token:
            headers["Authorization"] = token
        req = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers=headers)
        try:
            with self.opener.open(req, timeout=25) as response:
                body = response.read(MAX_RESPONSE + 1)
            if len(body) > MAX_RESPONSE:
                raise APIError("Response exceeds the size limit")
            result = json.loads(body)
        except urllib.error.HTTPError as exc:
            raise APIError(f"Octopus HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise APIError("Octopus connection or response failed") from None
        if not isinstance(result, dict):
            raise APIError("Octopus returned an invalid GraphQL response")
        if result.get("errors"):
            # Do not echo server messages, which may contain supplied inputs or private data.
            codes = [e.get("extensions", {}).get("errorCode", "GRAPHQL_ERROR") for e in result["errors"]]
            raise APIError("Octopus rejected the request: " + ", ".join(str(c) for c in codes))
        if not isinstance(result.get("data"), dict):
            raise APIError("Octopus returned no data")
        return result["data"]

    def authenticate(self, fields):
        response = self._request("authenticate", {"input": fields})["obtainKrakenToken"]
        token = response.get("token")
        if not token:
            raise APIError("Authentication returned no session")
        session = {"token": token, "expires_at": expiry(token),
                   "refresh_token": response.get("refreshToken"),
                   "refresh_expires_at": response.get("refreshExpiresIn"),
                   "obtained_at": time.time()}
        atomic_json(self.session_path, session)
        return session

    def session(self):
        try:
            session = json.loads(self.session_path.read_text())
        except (OSError, ValueError):
            raise APIError("No local Octopus session; run login first") from None
        if session["expires_at"] < time.time() + 30:
            if not session.get("refresh_token") or (session.get("refresh_expires_at") or 0) <= time.time():
                raise APIError("Session expired; sign in again")
            session = self.authenticate({"refreshToken": session["refresh_token"]})
        return session

    def read(self, operation, **variables):
        if operation == "authenticate" or operation not in OPERATIONS:
            raise APIError("Only read operations are allowed")
        return self._request(operation, variables, self.session()["token"])

    def download_bill(self, account, bill, destination):
        result = self.read("document", account=account, bill=bill)
        document = (result.get("account") or {}).get("bill")
        if not document:
            raise APIError("The bill has no accessible document")
        candidates = [e["node"] for e in document["attachments"]["edges"]
                      if (e["node"].get("filename") or "").lower().endswith(".pdf")]
        if len(candidates) != 1:
            raise APIError("Expected one PDF attachment; inspect this bill before downloading")
        url = candidates[0]["temporaryUrl"]
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        allowed = ("octopusenergy.es", "oees-kraken.energy", "amazonaws.com")
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise APIError("Unsupported document URL")
        if not any(host == suffix or host.endswith("." + suffix) for suffix in allowed):
            raise APIError("PDF storage hostname needs verification: " + host)
        try:
            # Signed URL only; never forward the API Authorization header to file storage.
            with self.opener.open(urllib.request.Request(url), timeout=30) as response:
                content = response.read(8 * 1024 * 1024 + 1)
        except (urllib.error.URLError, TimeoutError):
            raise APIError("PDF download failed") from None
        if len(content) > 8 * 1024 * 1024 or not content.startswith(b"%PDF-"):
            raise APIError("Invalid PDF or file exceeds 8 MiB")
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        name = "octopus-" + hashlib.sha256(bill.encode()).hexdigest()[:16] + ".pdf"
        path = destination / name
        fd, temp = tempfile.mkstemp(prefix=".invoice-", dir=destination)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return {"path": str(path.resolve()), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    login = commands.add_parser("login")
    login.add_argument("--email", help="Explicit one-time legacy password login; password is never stored")
    commands.add_parser("status")
    commands.add_parser("accounts")
    overview = commands.add_parser("overview")
    overview.add_argument("--account", required=True)
    bills = commands.add_parser("bills")
    bills.add_argument("--account", required=True)
    bills.add_argument("--ledger")
    bills.add_argument("--after")
    invoice = commands.add_parser("invoice")
    invoice.add_argument("--account", required=True)
    invoice.add_argument("--bill", required=True)
    invoice.add_argument("--after")
    measurements = commands.add_parser("measurements")
    for flag in ("property", "supply", "start", "end"):
        measurements.add_argument("--" + flag, required=True)
    measurements.add_argument("--after")
    download = commands.add_parser("download")
    download.add_argument("--account", required=True)
    download.add_argument("--bill", required=True)
    download.add_argument("--destination", default="output/pdf")
    args = parser.parse_args()
    client = Client()
    try:
        if args.command == "login":
            secret = getpass.getpass("Octopus password (hidden): " if args.email else "Octopus API key (hidden): ")
            if not secret:
                raise APIError("Empty credential")
            fields = {"email": args.email, "password": secret} if args.email else {"APIKey": secret}
            session = client.authenticate(fields)
            del fields, secret
            print(json.dumps({"authenticated": True, "refresh_available": bool(session["refresh_token"]),
                              "expires_at": session["expires_at"], "refresh_expires_at": session["refresh_expires_at"]}))
        elif args.command == "status":
            session = json.loads(client.session_path.read_text()) if client.session_path.exists() else {}
            print(json.dumps({"session_present": bool(session), "expires_at": session.get("expires_at"),
                              "refresh_expires_at": session.get("refresh_expires_at")}))
        elif args.command == "download":
            print(json.dumps(client.download_bill(args.account, args.bill, args.destination), indent=2))
        else:
            variables = vars(args).copy()
            variables.pop("command")
            if args.command == "measurements":
                start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
                if not 0 < (end - start).days <= 366:
                    raise APIError("Measurement range must be 1 to 366 days")
            print(json.dumps(client.read(args.command, **variables), ensure_ascii=False, indent=2))
    except (APIError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
