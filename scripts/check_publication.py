#!/usr/bin/env python3
"""Check the reviewed text-only publication manifest; never print suspect values."""
from pathlib import Path
import re
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
allowed = set(subprocess.check_output(['git','show',':PUBLIC_FILES.txt'],cwd=root).decode().splitlines())
files = subprocess.check_output(['git','ls-files','-z'], cwd=root).decode().split('\0')
patterns = {
    'private remote hostname': r'https?://[^\s/]+\.ts\.net',
    'personal absolute path': r'/Users/[A-Za-z][^/\s]+/',
    'JWT': r'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}',
    'GitHub token': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})',
    'private key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'certificate': r'-{5}BEGIN CERTIFICATE-{5}',
    'access key': r'\bAKIA[A-Z0-9]{16}\b',
}
failures=[]
for name in filter(None,files):
    path=root/name
    if name not in allowed:
        failures.append(f'{name}: not in reviewed manifest');continue
    if path.is_symlink():
        failures.append(f'{name}: symlink');continue
    content=subprocess.check_output(['git','show',':'+name],cwd=root)
    if len(content)>512*1024:
        failures.append(f'{name}: exceeds text file size limit');continue
    try: text=content.decode()
    except UnicodeError:
        failures.append(f'{name}: binary data');continue
    for label,pattern in patterns.items():
        if re.search(pattern,text):failures.append(f'{name}: {label}')
    for email in re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',text):
        if not email.endswith(('@example.invalid','@example.com','@users.noreply.github.com')):
            failures.append(f'{name}: non-example email address')
if failures:
    print('\n'.join(failures));sys.exit(1)
print(f'Publication checks passed: {len(list(filter(None,files)))} reviewed text files')
