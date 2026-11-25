"""Regression for the actual mixed-upload failure (old hidden workflow, new installer)."""
from __future__ import annotations
import importlib.util
from pathlib import Path
import os
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_check', ROOT/'scripts/verify_release_contract.py')
release_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_check)

class ReleaseContractSingleSourceTests(unittest.TestCase):
    def test_release_version_is_not_hardcoded_in_github_workflow(self):
        text = (ROOT/'.github/workflows/build.yml').read_text('utf-8')
        self.assertIn('tag_name: ${{ steps.release_version.outputs.tag }}',text)
        self.assertNotIn('tag_name: v0.9.1-beta.1',text)
        self.assertNotIn('tag_name: v1.0.0-rc.1',text)
        self.assertEqual(release_check.check(), 'v1.0.0-rc.2')

    def _fixture(self, temp_dir):
        root=Path(temp_dir)
        for name in ('scripts/build_installer.py','.github/workflows/build.yml'):
            dest=root/name
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes((ROOT/name).read_bytes())
        return root

    def test_old_hidden_workflow_is_rejected_with_actionable_message(self):
        with tempfile.TemporaryDirectory() as td:
            root=self._fixture(td)
            file=root/'.github/workflows/build.yml'
            file.write_text(file.read_text('utf-8').replace(
                'tag_name: ${{ steps.release_version.outputs.tag }}',
                'tag_name: v0.9.1-beta.1'), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, r'hidden \.github/workflows/build\.yml'):
                release_check.check(root)

    def test_new_tag_in_installer_requires_no_second_workflow_version_edit(self):
        with tempfile.TemporaryDirectory() as td:
            root=self._fixture(td)
            installer=root/'scripts/build_installer.py'
            installer.write_text(installer.read_text('utf-8').replace(
                "RELEASE_TAG = 'v1.0.0-rc.2'", "RELEASE_TAG = 'v1.0.0-rc.3'"),encoding='utf-8')
            self.assertEqual(release_check.check(root), 'v1.0.0-rc.3')

    def test_version_step_must_be_present_and_uncoupled_from_old_tag(self):
        with tempfile.TemporaryDirectory() as td:
            root=self._fixture(td)
            workflow=root/'.github/workflows/build.yml'
            workflow.write_text(workflow.read_text('utf-8').replace(
                'id: release_version', 'id: deleted_version'),encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'id: release_version'):
                release_check.check(root)

    def test_release_step_sets_github_output_to_verified_tag(self):
        # Use a real subprocess to test __main__ and --github-output, not a mock.
        import subprocess,sys
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'github_output.txt'
            env={**os.environ,'GITHUB_OUTPUT':str(output)}
            result=subprocess.run([sys.executable,str(ROOT/'scripts/verify_release_contract.py'),
                                   '--github-output'],capture_output=True,text=True,env=env)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
            self.assertEqual(output.read_text(encoding='utf-8'),'tag=v1.0.0-rc.2\n')

    def test_preflight_occurs_before_installation_and_model_download(self):
        text=(ROOT/'.github/workflows/build.yml').read_text('utf-8')
        self.assertLess(text.index('Validate release contract BEFORE installing large dependencies'),
                        text.index('Install build dependencies'))
        self.assertLess(text.index('Validate release contract BEFORE installing large dependencies'),
                        text.index('Download and checksum BOTH models'))

    def test_publication_requires_both_built_jobs_and_explicit_opt_in(self):
        text=(ROOT/'.github/workflows/build.yml').read_text('utf-8')
        self.assertIn('needs: build',text)
        self.assertIn('inputs.publish_beta == true',text)
        self.assertIn('prerelease: true',text)
        self.assertIn('target_commitish: ${{ github.sha }}',text)

if __name__=='__main__': unittest.main()
