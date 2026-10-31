from pathlib import Path
import re
import unittest

from macvm import grants, project

LODY = project.load(Path(__file__).parent / 'fixtures/lody.toml')


def rows(script):
    return re.findall(r"^grant (?:system|user) (\w+) '([^']+)' (\d) '([^']+)'$", script, re.M)


class BaseGrantsTest(unittest.TestCase):
    def test_every_controller_gets_every_system_service(self):
        script = grants.base_script()
        found = {(service, client) for service, client, _, _ in rows(script)}
        for client in ['/usr/libexec/sshd-keygen-wrapper', 'com.apple.Terminal', '/usr/bin/osascript']:
            for service in ['kTCCServiceAccessibility', 'kTCCServicePostEvent', 'kTCCServiceListenEvent',
                            'kTCCServiceScreenCapture', 'kTCCServiceSystemPolicyAllFiles', 'kTCCServiceDeveloperTool']:
                self.assertIn((service, client), found)

    def test_homebrew_tools_are_granted_by_their_resolved_path(self):
        script = grants.base_script()
        self.assertIn('readlink -f /opt/homebrew/bin/cliclick', script)
        self.assertIn('readlink -f /opt/homebrew/bin/peekaboo', script)

    def test_controllers_may_drive_system_events_and_finder_through_the_user_database(self):
        script = grants.base_script()
        apple_events = [(client, target) for service, client, _, target in rows(script) if service == 'kTCCServiceAppleEvents']
        self.assertIn(('/usr/libexec/sshd-keygen-wrapper', 'com.apple.systemevents'), apple_events)
        self.assertIn(('com.apple.Terminal', 'com.apple.finder'), apple_events)
        self.assertIn('/private/var/containers/Data/ProtectedSystem', script)
        self.assertIn('pgrep -u "$USER" -x tccd', script)

    def test_tccd_rereads_after_the_writes(self):
        self.assertTrue(grants.base_script().rstrip().endswith('sudo killall tccd 2>/dev/null; true'))


class AppGrantsTest(unittest.TestCase):
    def test_apps_start_undetermined_each_run(self):
        script = grants.app_script(LODY)
        self.assertIn("tccutil reset All 'app.innei.lody.mac'", script)
        self.assertIn("sudo tccutil reset All 'app.innei.lody.mac'", script)
        self.assertNotIn('kTCCServiceCamera', script)

    def test_only_configured_services_are_granted(self):
        configured = project.Project(**{**LODY.__dict__, 'grants': project.Grants(
            targets=[], apps={'app.example': ['camera', 'accessibility']})})
        found = {(service, client) for service, client, _, _ in rows(grants.app_script(configured))}
        self.assertEqual(found, {('kTCCServiceCamera', 'app.example'), ('kTCCServiceAccessibility', 'app.example')})

    def test_targets_accept_apple_events_from_controllers(self):
        found = [(client, target) for service, client, _, target in rows(grants.app_script(LODY))
                 if service == 'kTCCServiceAppleEvents']
        self.assertIn(('/usr/libexec/sshd-keygen-wrapper', 'app.innei.lody.mac'), found)

    def test_notifications_are_reset_and_granted_outside_tcc(self):
        configured = project.Project(**{**LODY.__dict__, 'grants': project.Grants(
            targets=[], apps={'app.example': ['notifications']})})
        script = grants.app_script(configured)
        self.assertIn('com.apple.ncprefs', script)
        self.assertIn("'app.example'", script)
        self.assertEqual(rows(script), [])

    def test_a_project_without_grants_still_runs_cleanly(self):
        empty = project.Project(**{**LODY.__dict__, 'grants': project.Grants()})
        self.assertEqual(grants.app_script(empty).strip(), 'true')


if __name__ == '__main__':
    unittest.main()
