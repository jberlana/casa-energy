"""Bounded private invoice discovery/download; never promotes PDF data to verified costs."""
import hashlib
import json
from pathlib import Path
import shutil
import time

from .octopus_api import APIError, Client, atomic_json

MIB = 1024 * 1024


class ConfigurationRequired(Exception):
    pass


class SyncClient(Client):
    def __init__(self, root):
        super().__init__(root)
        self.requests = 0

    def _request(self, operation, variables=None, token=None):
        if self.requests >= 12:
            raise APIError('Sync request limit reached')
        self.requests += 1
        return super()._request(operation, variables, token)


def sync(root, client=None):
    root = Path(root)
    client = client or SyncClient(root)
    config = json.loads((root/'config.json').read_text()) if (root/'config.json').exists() else {}
    account = config.get('account')
    if not account:
        accounts = client.read('accounts')['viewer']['accounts']
        if len(accounts) != 1:
            raise ConfigurationRequired('Select an account in private configuration')
        account = accounts[0]['number']
    ledgers = client.read('overview', account=account)['account']['ledgers']
    ledger = config.get('ledger')
    if ledger:
        if not any(row['number'] == ledger for row in ledgers):
            raise ConfigurationRequired('Unknown configured ledger')
    else:
        gas = [r['number'] for r in ledgers if 'GAS' in str(r.get('ledgerType', '')).upper()]
        if len(gas) != 1:
            raise ConfigurationRequired('Select a gas ledger in private configuration')
        ledger = gas[0]
    connection = client.read('bills', account=account, ledger=ledger)['account']['bills']
    bills = [e['node'] for e in connection['edges'] if e['node'].get('billType') == 'INVOICE' and not e['node'].get('isAnnulled')]
    if len(connection['edges']) > 20:
        raise APIError('Unexpected invoice page size')
    bills.sort(key=lambda b: b.get('issuedDate') or '', reverse=True)
    documents = root/'documents'
    documents.mkdir(parents=True, exist_ok=True, mode=0o700)
    records, downloaded, pending = [], 0, 0
    for bill in bills:
        # Scope document identity to the account, even when the upstream bill ID is reused.
        digest = hashlib.sha256((str(account) + ':' + str(bill['id'])).encode()).hexdigest()[:24]
        folder = documents/digest
        filename = 'octopus-' + hashlib.sha256(str(bill['id']).encode()).hexdigest()[:16] + '.pdf'
        path = folder/filename
        cached = path.is_file() and 5 <= path.stat().st_size <= 8*MIB
        if cached:
            with path.open('rb') as stream:
                cached = stream.read(5) == b'%PDF-'
        if not cached and downloaded < 3:
            used = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
            if used + 8*MIB > 64*MIB or shutil.disk_usage(root).free < 1024*MIB:
                raise APIError('Private invoice storage limit reached')
            client.download_bill(account, bill['id'], folder)
            downloaded += 1
            cached = True
        if not cached:
            pending += 1
        # Minimal private catalog; signed URLs and authentication are never persisted here.
        records.append({key: bill.get(key) for key in ('id', 'identifier', 'fromDate', 'toDate', 'issuedDate', 'grossAmount')} |
                       {'document': str(path.relative_to(root)) if cached else None})
    summary = {'updated': int(time.time()*1000), 'downloaded': downloaded, 'documents': sum(r['document'] is not None for r in records),
               'remaining': pending, 'moreHistory': bool(connection['pageInfo']['hasNextPage']), 'requiresVerification': bool(records)}
    # Atomic replacement only after a successful page; earlier verified invoices are never touched.
    catalog = {'account': account, 'ledger': ledger, 'bills': records, 'summary': summary}
    size = len(json.dumps(catalog, ensure_ascii=False, indent=2).encode()) + 1
    if size > MIB or sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) + size > 64*MIB:
        raise APIError('Private invoice storage limit reached')
    atomic_json(root/'catalog.json', catalog)
    return 7 if pending else 6 if records else 0
