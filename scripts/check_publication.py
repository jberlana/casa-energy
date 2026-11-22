#!/usr/bin/env python3
"""Check staged files and commit privacy without printing matched values."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

PATTERNS = {
    'private remote hostname': r'\b[\w.-]+\.ts\.net\b',
    'personal absolute path': r'/Users/[A-Za-z][^/\s]+/',
    'JWT': r'eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}',
    'GitHub token': r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})',
    'private key': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'certificate': r'-{5}BEGIN CERTIFICATE-{5}',
    'access key': r'\bAKIA[A-Z0-9]{16}\b',
}
EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
ALIAS = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?')
PRIVATE_EMAIL = re.compile(r'(?:\d+\+)?[A-Za-z0-9][A-Za-z0-9-]*(?:\[bot\])?@users\.noreply\.github\.com')


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)


def text_failures(text):
    failures = [label for label, pattern in PATTERNS.items() if re.search(pattern, text)]
    if any(not email.endswith(('@example.invalid', '@example.com', '@users.noreply.github.com'))
           and email != 'noreply@github.com' for email in EMAIL.findall(text)):
        failures.append('non-example email address')
    return failures


def identity_failures(name, email):
    failures = []
    if not ALIAS.fullmatch(name):
        failures.append('use a GitHub alias instead of a personal name')
    if not PRIVATE_EMAIL.fullmatch(email) and (name, email) != ('GitHub', 'noreply@github.com'):
        failures.append('use a GitHub noreply email')
    return failures


def check(root, all_branches=False):
    failures = []
    allowed = set(git(root, 'show', ':PUBLIC_FILES.txt').decode().splitlines())
    entries = list(filter(None, git(root, 'ls-files', '--stage', '-z').split(b'\0')))
    for entry in entries:
        metadata, raw_name = entry.split(b'\t', 1)
        mode, oid, stage = metadata.decode().split()
        name = raw_name.decode()
        if name not in allowed:
            failures.append(f'{name}: not in reviewed manifest'); continue
        if stage != '0' or mode not in ('100644', '100755'):
            failures.append(f'{name}: unresolved or non-regular staged file'); continue
        content = git(root, 'cat-file', 'blob', oid)
        if len(content) > 512 * 1024:
            failures.append(f'{name}: exceeds text file size limit'); continue
        try:
            text = content.decode()
            if '\0' in text:
                raise UnicodeError()
        except UnicodeError:
            failures.append(f'{name}: binary data'); continue
        failures.extend(f'{name}: {label}' for label in text_failures(text))
    for role in ('AUTHOR', 'COMMITTER'):
        try:
            identity = git(root, 'var', f'GIT_{role}_IDENT').decode()
            name, email = re.match(r'(.*) <([^>]*)> ', identity).groups()
            failures.extend(f'next commit {role.lower()}: {label}' for label in identity_failures(name, email))
        except (subprocess.CalledProcessError, AttributeError):
            failures.append(f'next commit {role.lower()}: configure a GitHub alias and noreply email')
    refs = ['--branches', '--remotes=origin'] if all_branches else ['HEAD']
    try:
        commits = git(root, 'rev-list', *refs).decode().splitlines()
    except subprocess.CalledProcessError:
        commits = []  # First commit: only the pending identity exists.
    for commit in commits:
        fields = git(root, 'show', '-s', '--format=%an%x00%ae%x00%cn%x00%ce%x00%B', commit).decode().split('\0', 4)
        for role, name, email in [('author', *fields[:2]), ('committer', *fields[2:4])]:
            failures.extend(f'commit {commit[:12]} {role}: {label}' for label in identity_failures(name, email))
        failures.extend(f'commit {commit[:12]} message: {label}' for label in text_failures(fields[4]))
    return failures, len(entries), len(commits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all-branches', action='store_true', help='Check local and origin branch histories, excluding immutable PR archive refs')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        failures, files, commits = check(root, args.all_branches)
    except (subprocess.CalledProcessError, UnicodeError, ValueError):
        print('Publication check could not read the staged tree; resolve the index and retry.')
        return 1
    if failures:
        print('\n'.join(failures))
        return 1
    print(f'Publication checks passed: {files} reviewed text files, {commits} commits and pending identity')
    return 0


if __name__ == '__main__':
    sys.exit(main())
