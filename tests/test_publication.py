import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_publication import check


class PublicationPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            'GIT_AUTHOR_NAME': 'demo-user',
            'GIT_COMMITTER_NAME': 'demo-user',
            'GIT_AUTHOR_EMAIL': '123+demo-user@users.noreply.github.com',
            'GIT_COMMITTER_EMAIL': '123+demo-user@users.noreply.github.com',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git('init', '-b', 'main')
        (self.root / 'PUBLIC_FILES.txt').write_text('PUBLIC_FILES.txt\nexample.txt\n')
        (self.root / 'example.txt').write_text('Synthetic example\n')
        self.git('add', 'PUBLIC_FILES.txt', 'example.txt')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.DEVNULL)

    def test_private_author_in_history_is_rejected_after_identity_is_fixed(self):
        with patch.dict(os.environ, {'GIT_AUTHOR_EMAIL': 'person@example.invalid'}):
            self.git('commit', '-m', 'test: add synthetic example')
        failures, _, _ = check(self.root)
        self.assertTrue(any('author: use a GitHub noreply email' in failure for failure in failures))
        self.assertNotIn('person@example.invalid', '\n'.join(failures))

    def test_personal_name_is_rejected_even_with_a_private_email(self):
        with patch.dict(os.environ, {'GIT_AUTHOR_NAME': 'Example Person'}):
            failures, _, _ = check(self.root)
        self.assertTrue(any('personal name' in failure for failure in failures))
        self.assertNotIn('Example Person', '\n'.join(failures))

    def test_commit_messages_are_checked_without_printing_matches(self):
        email = 'person@' + 'mail.invalid'
        self.git('commit', '-m', 'test: add example\n\nContact ' + email)
        failures, _, _ = check(self.root)
        self.assertTrue(any('message: non-example email address' in failure for failure in failures))
        self.assertNotIn(email, '\n'.join(failures))

    def test_staged_content_is_checked_even_when_worktree_has_been_cleaned(self):
        email = 'person@' + 'mail.invalid'
        (self.root / 'example.txt').write_text(email)
        self.git('add', 'example.txt')
        (self.root / 'example.txt').write_text('Synthetic example\n')
        failures, _, _ = check(self.root)
        self.assertIn('example.txt: non-example email address', failures)
        self.assertNotIn(email, '\n'.join(failures))

    def test_staged_symlink_is_rejected_even_if_worktree_is_regular(self):
        path = self.root / 'example.txt'
        path.unlink()
        path.symlink_to('PUBLIC_FILES.txt')
        self.git('add', 'example.txt')
        path.unlink()
        path.write_text('Synthetic example\n')
        failures, _, _ = check(self.root)
        self.assertTrue(any('non-regular' in failure for failure in failures))

    def test_other_branch_identity_is_included_when_requested(self):
        self.git('commit', '-m', 'test: add synthetic example')
        self.git('checkout', '-b', 'other')
        with patch.dict(os.environ, {'GIT_AUTHOR_EMAIL': 'person@example.invalid'}):
            self.git('commit', '--allow-empty', '-m', 'test: cover branch history')
        self.git('checkout', 'main')
        self.assertEqual(check(self.root)[0], [])
        self.assertTrue(check(self.root, all_branches=True)[0])

    def test_private_alias_and_synthetic_content_pass(self):
        self.git('commit', '-m', 'test: add synthetic example')
        failures, files, commits = check(self.root)
        self.assertEqual(failures, [])
        self.assertEqual((files, commits), (2, 1))


if __name__ == '__main__':
    unittest.main()
